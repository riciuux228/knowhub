import base64
import os
import hashlib
import logging
import urllib.parse
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
import httpx
from datetime import datetime
import json

logger = logging.getLogger("knowhub")

from backend.config import DATA_DIR, events
from backend.database import get_db
from backend.ai_services import ai_summarize_and_tag, get_embedding
from backend.qmd.chunker import chunk_document

def wechat_decrypt_aes_ecb(ciphertext: bytes, key: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    if not padded: return padded
    pad_len = padded[-1]
    if pad_len > algorithms.AES.block_size or pad_len == 0:
        return padded
    return padded[:-pad_len]

def wechat_encrypt_aes_ecb(plaintext: bytes, key: bytes) -> bytes:
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()

async def download_wechat_media(encrypt_query_param: str, aes_key_base64: str) -> bytes:
    aes_key_hex_bytes = base64.b64decode(aes_key_base64)
    aes_key = bytes.fromhex(aes_key_hex_bytes.decode('utf-8'))
    cdn_url = f"https://novac2c.cdn.weixin.qq.com/c2c/download?encrypted_query_param={urllib.parse.quote(encrypt_query_param)}"
    async with httpx.AsyncClient() as hc:
        res = await hc.get(cdn_url, timeout=60)
        res.raise_for_status()
        return wechat_decrypt_aes_ecb(res.content, aes_key)

async def upload_wechat_media(client, to_user_id: str, media_type: int, data: bytes):
    filekey = os.urandom(16)
    aeskey = os.urandom(16)
    filekey_hex = filekey.hex()
    aeskey_hex = aeskey.hex()
    raw_md5 = hashlib.md5(data).hexdigest()
    
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(data) + padder.finalize()
    cipher_size = len(padded)
    
    resp_data = await client.get_upload_url(
        filekey_hex, media_type, to_user_id, len(data), raw_md5, cipher_size, aeskey_hex
    )
    
    encrypted = wechat_encrypt_aes_ecb(data, aeskey)
    
    cdn_url = resp_data.get('upload_full_url', "").strip()
    if not cdn_url:
        cdn_url = f"https://novac2c.cdn.weixin.qq.com/c2c/upload?encrypted_query_param={urllib.parse.quote(resp_data.get('upload_param', ''))}&filekey={urllib.parse.quote(filekey_hex)}"
        
    async with httpx.AsyncClient() as hc:
        res = await hc.post(cdn_url, content=encrypted, headers={"Content-Type": "application/octet-stream"}, timeout=60.0)
        res.raise_for_status()
    
    download_param = res.headers.get("X-Encrypted-Param", "")
    aes_key_base64 = base64.b64encode(aeskey_hex.encode('utf-8')).decode('utf-8')
    return download_param, aes_key_base64, len(data), cipher_size

ocr_reader = None

async def _extract_image_vlm(content: bytes, mime: str) -> str:
    from backend.config import load_config
    cfg = load_config()
    v_key = cfg.get("VISION_API_KEY", "").strip()
    v_url = cfg.get("VISION_BASE_URL", "").strip()
    v_model = cfg.get("VISION_MODEL", "").strip()

    if not v_model and not v_key:
        raise Exception("多模态专线 (VLM) 尚未配置")

    import base64
    import io
    from openai import AsyncOpenAI
    
    # Compress the image before converting to VLM base64
    content_to_encode = content
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(content))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        max_size = 1024
        if img.width > max_size or img.height > max_size:
            img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            output_format = "JPEG" if mime in ["image/jpeg", "image/jpg"] else "PNG"
            img.save(buffer, format=output_format, quality=85)
            content_to_encode = buffer.getvalue()
            print(f"📉 [VLM瘦身] 图片分辨率已从原质压缩拦截并动态降级至安全尺寸")
    except Exception as e:
        print(f"PIL 图片压缩失败 ({e}), 退化为原始字节流...")
    
    b64_img = base64.b64encode(content_to_encode).decode('utf-8')
    data_url = f"data:{mime};base64,{b64_img}"
    
    ai_key = v_key or cfg.get("AI_API_KEY", "").strip()
    ai_url = v_url or cfg.get("AI_BASE_URL", "https://api.openai.com/v1").strip()
    ai_model = v_model or "gpt-4o"
    
    client = AsyncOpenAI(api_key=ai_key, base_url=ai_url)
    resp = await client.chat.completions.create(
        model=ai_model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "请深层提取及解释此图中的有效结构化文本、代码或意图含义。如果是截图、梗图，请生动解释它的内容；如果包含报表或代码，请格式化输出。"},
                {"type": "image_url", "image_url": {"url": data_url}}
            ]
        }],
        max_tokens=2000
    )
    return resp.choices[0].message.content

def _extract_image(content: bytes) -> str:
    global ocr_reader
    if ocr_reader is None:
        import easyocr
        print("首次启动 OCR，正在加载 EasyOCR AI 视觉网络 (ch_sim, en)...")
        ocr_reader = easyocr.Reader(['ch_sim', 'en'], gpu=False) 
        print("OCR 网络加载完成！")
    results = ocr_reader.readtext(content, detail=0, paragraph=True)
    return "\n".join(results)

def _extract_with_markitdown(file_path: str) -> str:
    """使用 MarkItDown 将文件统一转换为结构化 Markdown 文本
    启用 OCR 插件：PDF/PPT/Excel 中的嵌入图片也会被识别
    """
    from markitdown import MarkItDown
    try:
        # Try with OCR plugin using our configured VLM
        from backend.config import load_config
        from openai import OpenAI
        cfg = load_config()
        v_key = cfg.get("VISION_API_KEY", "").strip() or cfg.get("AI_API_KEY", "").strip()
        v_url = cfg.get("VISION_BASE_URL", "").strip() or cfg.get("AI_BASE_URL", "").strip()
        v_model = cfg.get("VISION_MODEL", "").strip() or "gpt-4o"
        
        if v_key and v_key != "sk-xxx":
            client = OpenAI(api_key=v_key, base_url=v_url)
            md = MarkItDown(enable_plugins=True, llm_client=client, llm_model=v_model)
        else:
            md = MarkItDown(enable_plugins=False)
    except Exception:
        md = MarkItDown(enable_plugins=False)
    
    import re
    result = md.convert(file_path)
    text = result.text_content or ""
    # 替换 MarkItDown 默认掐断的已损坏 base64 图像数据
    text = re.sub(r'!\[([^\]]*)\]\(data:image/[^;]+;base64\.\.\.\)', r'[🖼️ 提取出配图: \1]', text)
    return text

import asyncio

# ===== Format routing tables =====
# Code/text files: read as UTF-8 directly (best quality for source code)
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
    ".md", ".txt", ".css", ".scss", ".less",
    ".java", ".c", ".cpp", ".h", ".hpp", ".go", ".rs", ".swift", ".kt",
    ".sh", ".bat", ".ps1", ".toml", ".ini", ".cfg", ".env",
    ".sql", ".r", ".rb", ".php", ".lua", ".vim",
    ".dockerfile", ".makefile", ".cmake",
}
# Image files: use VLM/OCR pipeline (specialized, higher quality)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}
# MarkItDown-powered document formats (structured conversion to Markdown)
MARKITDOWN_EXTENSIONS = {
    ".pdf", ".docx", ".doc",          # Documents
    ".pptx", ".ppt",                  # Presentations
    ".xlsx", ".xls",                  # Spreadsheets
    ".html", ".htm",                  # Web pages
    ".csv",                           # Tabular data
    ".xml",                           # Structured data  
    ".zip",                           # Archives (iterates contents)
    ".epub",                          # E-books
}


async def process_binary_file(item_id: str, content: bytes, filename: str, space: str = "default", force_title=""):
    import mimetypes
    import hashlib
    from pathlib import Path
    ext = Path(filename).suffix
    save_name = f"{item_id}{ext}"
    save_path = DATA_DIR / save_name

    # Step 0: 精确重复检测
    content_hash = hashlib.sha256(content).hexdigest()
    try:
        conn_check = get_db()
        existing = conn_check.execute(
            "SELECT h.item_id, i.title FROM content_hashes h JOIN items i ON i.id = h.item_id WHERE h.hash = ?",
            (content_hash,)
        ).fetchone()
        conn_check.close()
        if existing:
            await events.publish(f"⚠️ [File] 检测到重复内容: {filename} 与已有记录「{existing['title']}」完全相同，跳过保存")
            return {"ok": True, "id": existing["item_id"], "duplicate": True,
                    "summary": f"与已有记录「{existing['title']}」内容完全相同", "tags": [], "category": ""}
    except Exception:
        pass

    # Step 1: 永远先落盘保存源文件（保留下载功能）
    with open(save_path, "wb") as f:
        f.write(content)

    ext_lower = ext.lower()
    if ext_lower in IMAGE_EXTENSIONS:
        mime = f"image/{ext_lower.replace('.', '').replace('jpg', 'jpeg')}"
    else:
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        
    await events.publish(f"📥 [File] 成功接收二进制流并落盘保存: {filename} ({(len(content)/1024):.2f} KB)")

    # Step 2: 根据文件类型分流提取文本内容
    text_content = ""
    
    if mime.startswith("text/") or ext_lower in CODE_EXTENSIONS:
        # === 代码/纯文本：直接 UTF-8 读取（最高保真度）===
        try:
            text_content = content.decode("utf-8", errors="replace")
        except Exception as e:
            logger.debug("UTF-8 decode failed for %s: %s", filename, e)
            
    elif ext_lower in IMAGE_EXTENSIONS:
        # === 图片：VLM 多模态 → OCR 双降级管道 ===
        try:
            text_content = await _extract_image_vlm(content, mime)
            if text_content.strip():
                print(f"🖼 [VLM 多模态提取成功] -> {text_content[:50]}...")
        except Exception as vlm_e:
            print(f"VLM 多模态脱水通道不可用 ({vlm_e})，自动回旋降级至本地离线 OCR...")
            try:
                text_content = await asyncio.to_thread(_extract_image, content)
                if text_content.strip():
                    print(f"🖼 [本地 OCR 补救提取成功] -> {text_content[:50]}...")
            except ImportError:
                print("easyocr 未安装，且多模态 VLM 失效，图片降维失败。")
            except Exception as e:
                print(f"图片 OCR 分析彻底失败: {e}")
                
    elif ext_lower in MARKITDOWN_EXTENSIONS:
        # === 文档/表格/演示/网页/压缩包：MarkItDown 统一转换引擎 ===
        format_names = {
            ".pdf": "PDF", ".docx": "Word", ".doc": "Word",
            ".pptx": "PPT", ".ppt": "PPT",
            ".xlsx": "Excel", ".xls": "Excel",
            ".html": "HTML", ".htm": "HTML",
            ".csv": "CSV", ".xml": "XML",
            ".zip": "ZIP", ".epub": "EPub",
        }
        fmt_name = format_names.get(ext_lower, ext_lower.upper())
        await events.publish(f"📄 [MarkItDown] 正在解析 {fmt_name} 文件: {filename}...")
        try:
            text_content = await asyncio.to_thread(_extract_with_markitdown, str(save_path))
            char_count = len(text_content)
            await events.publish(f"✅ [MarkItDown] {fmt_name} 转换完成，提取 {char_count} 字符结构化 Markdown")
        except Exception as e:
            print(f"MarkItDown conversion error for {filename}: {e}")
            await events.publish(f"❌ [MarkItDown] {fmt_name} 解析失败: {str(e)}")
            text_content = f"文件解析失败: {str(e)}"
    else:
        # === 未知格式：尝试 MarkItDown 万能降级 ===
        try:
            text_content = await asyncio.to_thread(_extract_with_markitdown, str(save_path))
            if text_content.strip():
                await events.publish(f"🔄 [MarkItDown] 未知格式 {ext_lower} 成功降级提取")
        except Exception as e:
            logger.debug("MarkItDown fallback extraction failed for %s: %s", ext_lower, e)

    # Step 3: AI 摘要 + 标签
    title_to_use = force_title if force_title.strip() else filename
    info = await ai_summarize_and_tag(title_to_use, text_content, filename)
    final_title = title_to_use if force_title.strip() else (info.get("title") or filename)
    item_type = 'image' if ext_lower in IMAGE_EXTENSIONS else 'file'

    # Step 4: 入库（file_path 保留源文件路径，确保下载功能正常）
    actual_space = info.get("space", "default") if space == "auto" else space
    
    now = datetime.now().isoformat()
    try:
        conn = get_db()
        conn.execute("""
            INSERT INTO items (id, type, title, content, file_path, file_size, mime_type, tags, summary, space, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (item_id, item_type, final_title, text_content[:10000], str(save_path),
              len(content), mime, json.dumps(info.get("tags", []), ensure_ascii=False),
              info.get("summary", ""), actual_space, now, now))
        conn.commit()
    finally:
        conn.close()

    # Step 5: 全文向量嵌入
    await events.publish(f"🛠️ [AI] 提取文档特征向量并存入数据库...")
    embed_text = f"{final_title}\n{info.get('summary', '')}\n{text_content[:2000]}"
    vec = await get_embedding(embed_text)

    try:
        conn2 = get_db()
        conn2.execute("INSERT INTO embeddings (item_id, vector) VALUES (?, ?)",
                     (item_id, vec.tobytes()))
        # Store content hash for duplicate detection
        conn2.execute("INSERT OR IGNORE INTO content_hashes (hash, item_id, created_at) VALUES (?, ?, ?)",
                     (content_hash, item_id, now))
        conn2.commit()
    finally:
        conn2.close()

    # gitmem0: 存储文件摘要到 agent 记忆库
    gm_stored = False
    try:
        from backend.gitmem0_client import remember as gm_remember
        gm_resp = await gm_remember(
            f"{final_title}\n{info.get('summary', '')}",
            type="fact", importance=0.6, source="knowhub:file",
            tags=info.get("tags", [])
        )
        gm_stored = gm_resp.get("ok", False)
    except Exception as e:
        logger.debug("gitmem0 remember failed for file %s: %s", filename, e)

    # Step 6: QMD 智能分块 + 分块嵌入（提升段落级检索精度）
    if len(text_content) > 500:
        chunks = chunk_document(text_content)
        if len(chunks) > 1:
            await events.publish(f"🧩 [Chunk] 文档已切分为 {len(chunks)} 个语义块，正在生成分块向量...")
            try:
                conn3 = get_db()
                for i, chunk in enumerate(chunks):
                    chunk_vec = await get_embedding(chunk["text"][:2000])
                    conn3.execute(
                        "INSERT INTO chunks (item_id, chunk_index, chunk_text, chunk_pos, vector) VALUES (?, ?, ?, ?, ?)",
                        (item_id, i, chunk["text"], chunk["pos"], chunk_vec.tobytes())
                    )
                conn3.commit()
                conn3.close()
                await events.publish(f"✅ [Chunk] {len(chunks)} 个分块向量全部生成完成")
            except Exception as e:
                print(f"Chunk embedding error: {e}")

    return {"ok": True, "id": item_id, "summary": info.get("summary", ""),
            "tags": info.get("tags", []), "category": info.get("category", ""),
            "memory_stored": gm_stored, "_vec": vec}
