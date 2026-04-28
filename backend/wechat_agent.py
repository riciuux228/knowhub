import os
import json
import logging
import uuid
import re
import asyncio
from datetime import datetime
import httpx

logger = logging.getLogger("knowhub")

from backend import weclaw_bot
from backend.config import DATA_DIR, events
from backend.database import get_db
from backend.file_services import download_wechat_media, upload_wechat_media, process_binary_file
from backend.gitmem0_client import query_context as gm_query, extract as gm_extract

class ChatMemoryManager:
    def __init__(self, max_turns=6):
        self.memory = {}
        self.max_turns = max_turns
        self._trim_threshold = max_turns * 4  # trim when 2x over limit

    def add(self, sender, message):
        if sender not in self.memory:
            self.memory[sender] = []
        self.memory[sender].append(message)
        # Proactively trim to prevent unbounded growth
        if len(self.memory[sender]) > self._trim_threshold:
            self._trim(sender)

    def _trim(self, sender):
        history = self.memory.get(sender, [])
        if len(history) <= self.max_turns * 2:
            return
        cutoff = len(history) - self.max_turns * 2
        # Align to a user message boundary
        for i in range(cutoff, len(history)):
            if isinstance(history[i], dict) and history[i].get("role") == "user":
                self.memory[sender] = history[i:]
                return
        self.memory[sender] = history[cutoff:]

    def get_history(self, sender):
        history = self.memory.get(sender, [])
        if len(history) > self.max_turns * 2:
            self._trim(sender)
            return self.memory.get(sender, [])
        return history

wx_chat_memory = ChatMemoryManager(max_turns=8)
wx_client = None  # Exposed for reminder_worker to send messages
wx_client_ready = asyncio.Event()  # Set when wx_client is connected and ready

async def start_wechat_worker():
    token_file = DATA_DIR / "wechat_token.json"
    sync_buf_file = DATA_DIR / "wechat_sync.json"
    saved_creds = {}
    if token_file.exists():
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                saved_creds = json.load(f)
        except (json.JSONDecodeError, TypeError): pass
        
    client = weclaw_bot.ILinkClient(
        bot_token=saved_creds.get("bot_token", ""),
        bot_id=saved_creds.get("ilink_bot_id", ""),
        base_url=saved_creds.get("base_url", "")
    )
    
    print(f"\n{'='*50}\n🌟 启动 WeChat-iLink 监听节点...\n{'='*50}")

    if not client.bot_token:
        print(">>> 检测到无历史登录票据，请扫码绑定服务区：")
        try:
            creds = await weclaw_bot.print_qr_and_poll_login()
            with open(token_file, "w", encoding="utf-8") as f:
                json.dump(creds, f)
            client = weclaw_bot.ILinkClient(
                bot_token=creds["bot_token"],
                bot_id=creds["ilink_bot_id"],
                base_url=creds.get("base_url")
            )
            print(f"✅ 微信授权凭证已保存，后续将自动登录。")
        except Exception as e:
            print(f"微信扫码模块由于网络抛错，请忽略：{e}")
            return
    else:
        print(">>> 检测到持久化微信权限令牌！正在免扫码重返长连接隧道...")
        
    print(f"\n{'='*50}\n✅ 微信节点连接成功，正在监听外部消息...\n{'='*50}")

    global wx_client
    wx_client = client
    wx_client_ready.set()

    async def send_client_text(to_id, text, ctx_token):
        MAX_LEN = 800
        if len(text) <= MAX_LEN:
            c_id = uuid.uuid4().hex
            msg = weclaw_bot.build_text_message(to_id, text, ctx_token, c_id)
            await client.send_message(msg)
            return

        # Split by double newlines (paragraphs), preserving structure
        parts = []
        current = ""
        for para in text.split("\n\n"):
            if len(current) + len(para) + 2 > MAX_LEN and current:
                parts.append(current.rstrip())
                current = para
            else:
                current = current + "\n\n" + para if current else para
        if current:
            parts.append(current.rstrip())

        # If any single paragraph exceeds MAX_LEN, hard-split by lines
        final_parts = []
        for p in parts:
            if len(p) <= MAX_LEN:
                final_parts.append(p)
            else:
                chunk = ""
                for line in p.split("\n"):
                    if len(chunk) + len(line) + 1 > MAX_LEN and chunk:
                        final_parts.append(chunk.rstrip())
                        chunk = line
                    else:
                        chunk = chunk + "\n" + line if chunk else line
                if chunk:
                    final_parts.append(chunk.rstrip())

        # If still too long (e.g. single super-long line), hard-split by chars
        really_final = []
        for p in final_parts:
            if len(p) <= MAX_LEN:
                really_final.append(p)
            else:
                for i in range(0, len(p), MAX_LEN):
                    really_final.append(p[i:i+MAX_LEN])

        total = len(really_final)
        for i, part in enumerate(really_final):
            prefix = f"[{i + 1}/{total}]\n" if total > 1 else ""
            c_id = uuid.uuid4().hex
            msg = weclaw_bot.build_text_message(to_id, prefix + part, ctx_token, c_id)
            await client.send_message(msg)
            if i < total - 1:
                await asyncio.sleep(0.5)

    async def handler(c: weclaw_bot.ILinkClient, msg: dict):
        if msg.get("message_type") != weclaw_bot.MSG_TYPE_USER: return
        if msg.get("message_state") != weclaw_bot.MSG_STATE_FINISH: return

        sender = msg.get("from_user_id", "")
        ctx_token = msg.get("context_token", "")
        print(f"[WeChat] Received message from {sender}", flush=True)
        
        content = weclaw_bot.extract_text(msg)
        media_item = {}
        is_image = False
        original_filename = ""
        media_url = ""
        
        if not content:
            content = weclaw_bot.extract_voice_text(msg)
            
        img_item = weclaw_bot.extract_image(msg)
        if img_item:
            is_image = True
            original_filename = f"wechat_image_{uuid.uuid4().hex[:6]}.jpg"
            if img_item.get("media"):
                media_item = img_item["media"]
            elif img_item.get("url"):
                media_url = img_item["url"]
                
        file_item = weclaw_bot.extract_file(msg)
        if file_item and file_item.get("media"):
            media_item = file_item["media"]
            original_filename = file_item.get("file_name", f"wechat_file_{uuid.uuid4().hex[:6]}.bin")
            
        content = content.strip()
        if not content and not media_item and not media_url: return
        
        try:
            if media_item or media_url:
                try:
                    await send_client_text(sender, "📥 正在下载系统加密文件并建立索引...", ctx_token)
                    
                    decrypted_data = None
                    if media_item:
                        decrypted_data = await download_wechat_media(media_item.get("encrypt_query_param", ""), media_item.get("aes_key", ""))
                    elif media_url:
                        async with httpx.AsyncClient() as hc:
                            res = await hc.get(media_url, timeout=60)
                            res.raise_for_status()
                            decrypted_data = res.content
                            
                    item_id = uuid.uuid4().hex[:12]
                    
                    await process_binary_file(item_id, decrypted_data, original_filename, space="default", force_title=original_filename)
                    
                    await send_client_text(sender, f"收到，「{original_filename}」已经存好啦~", ctx_token)
                except Exception as e:
                    print(f"Fetch WeChat Encrypted Media error: {e}")
                    await send_client_text(sender, f"⚠️ 脱壳拉取阻断：{str(e)}", ctx_token)
                    
                if not content: return
                
            await events.publish(f"💬 [WeChat_Hook] 捕获新消息: {content[:20]}...")
            print(f"[WeChat] Processing text message: '{content[:50]}...' (len={len(content)})", flush=True)
            
            try:
                await send_client_text(sender, "稍等，看看~", ctx_token)
            except Exception as e:
                logger.debug("WeChat send initial text failed: %s", e)

            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S %A")
            system_prompt = (
                f"你是用户微信里的私人助手。当前时间：{current_time}。\n\n"
                "性格设定：\n"
                "- 你是一个靠谱、有温度的朋友，不是冷冰冰的机器人\n"
                "- 说话自然随意，像微信聊天一样，别端着\n"
                "- 偶尔可以用语气词（嗯、哈、噢、哎、嘿嘿），但别过度\n"
                "- 回答简洁有用，别啰嗦，别写小作文\n"
                "- 不确定的事情直说不确定，别瞎编\n"
                "- 用户分享了好东西，可以适当表达兴趣（\"这个不错诶\"、\"收藏了\"）\n\n"
                "能力：\n"
                "- search_knowledge：搜知识库回答问题\n"
                "- get_raw_document：提取原件\n"
                "- save_to_brain：保存用户发来的内容（链接/代码/长文），必须调用，别嘴上说已保存\n"
                "- set_reminder：设提醒，时间用 ISO 8601\n"
                "- github_trending：查 GitHub 趋势\n"
                "- github_star_detail：查仓库详情\n"
                "- remember：记住重要信息\n"
                "- forget：用户说「忘掉XXX」时删除记忆\n\n"
                "排版（GitHub Flavored Markdown）：\n"
                "- 标题用 ## / ###\n"
                "- 代码块标语言\n"
                "- 列表用 - 或 1. 2.\n"
                "- 重点 **加粗**\n\n"
                "闲聊时不用调工具，正常聊天就行。"
            )

            # gitmem0: 查询用户历史记忆并注入 system prompt
            try:
                gm_ctx = await gm_query(content, budget=800)
                if gm_ctx.get("has_memories"):
                    system_prompt += f"\n\n[用户历史记忆]\n{gm_ctx['context']}"
            except Exception as e:
                logger.debug("gitmem0 query_context failed: %s", e)

            wx_chat_memory.add(sender, {"role": "user", "content": content})

            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(wx_chat_memory.get_history(sender))

            # ── Unified tools + ReAct loop ──
            from backend.tools import get_openai_tools, react_loop

            tools = get_openai_tools(channels=["wechat"])

            # WeChat-specific context for file sending
            async def _wx_send_file(target_path, item):
                media_type = weclaw_bot.CDN_MEDIA_TYPE_IMAGE if item.get('type') == 'image' else weclaw_bot.CDN_MEDIA_TYPE_FILE
                with open(target_path, "rb") as f:
                    raw_data = f.read()
                param, ak, size, csize = await upload_wechat_media(c, sender, media_type, raw_data)
                media_payload = {"encrypt_query_param": param, "aes_key": ak, "encrypt_type": 1}
                c_id = uuid.uuid4().hex
                if item.get('type') == 'image':
                    out_msg = weclaw_bot.build_image_message(sender, media_payload, size, ctx_token, c_id)
                else:
                    fname = os.path.basename(target_path)
                    out_msg = weclaw_bot.build_file_message(sender, media_payload, fname, str(size), ctx_token, c_id)
                await c.send_message(out_msg)

            tool_ctx = {
                "channel": "wechat",
                "sender": sender,
                "ctx_token": ctx_token,
                "send_file": _wx_send_file,
                "send_text": lambda text: send_client_text(sender, text, ctx_token),
            }

            # Run ReAct loop — AI decides which tools to call
            collected_reply = ""
            try:
                async for sse_chunk in react_loop(messages, tools, tool_ctx, max_rounds=10):
                    data_str = sse_chunk.replace("data: ", "").strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        if data.get("tool_call") and data.get("status") == "running":
                            tool_name = data["tool_call"]
                            tool_msgs = {
                                "search_knowledge": "翻翻知识库...",
                                "get_raw_document": "找找原文...",
                                "save_to_brain": "帮你存起来...",
                                "set_reminder": "设个提醒...",
                                "github_trending": "看看最近有啥热门项目...",
                                "github_star_detail": "查查这个项目...",
                                "remember": "记一下...",
                                "forget": "删掉这条记忆...",
                            }
                            msg = tool_msgs.get(tool_name, f"用一下 {tool_name}...")
                            await send_client_text(sender, msg, ctx_token)
                        if data.get("content"):
                            collected_reply += data["content"]
                    except (json.JSONDecodeError, TypeError): pass
            except Exception as e:
                print(f"[WeChat] react_loop error: {e}", flush=True)
                if not collected_reply:
                    await send_client_text(sender, "⚠️ 处理异常，请稍后重试。", ctx_token)

            # Send final reply
            if collected_reply:
                await send_client_text(sender, collected_reply, ctx_token)

            # Update message history for memory
            for msg in messages:
                if msg.get("role") in ("assistant", "tool") and msg not in wx_chat_memory.get_history(sender):
                    wx_chat_memory.add(sender, msg)

            # gitmem0: 只在有实质内容时才提取记忆
            _should_extract = (
                collected_reply
                and len(content) > 15
                and not any(kw in content for kw in ["你好", "hello", "hi", "嗨", "谢谢", "thanks"])
                and len(collected_reply) > 30
            )
            if _should_extract:
                try:
                    conv_summary = f"用户: {content}\n助手: {collected_reply}"
                    gm_result = await gm_extract(conv_summary, source=f"wechat:{sender}")
                    extracted = gm_result.get("extracted", 0)
                    if extracted > 0:
                        mem_list = gm_result.get("memories", [])
                        mem_preview = "\n".join([f"  · {m.get('content', '')[:60]}" for m in mem_list[:3]])
                        await send_client_text(sender, f"嗯嗯，帮你记住了 {extracted} 条：\n{mem_preview}\n\n想忘掉哪条跟我说「忘掉 XXX」就行", ctx_token)
                except Exception as e:
                    logger.debug("gitmem0 extract failed for sender %s: %s", sender, e)

        except Exception as e:
            try:
                await send_client_text(sender, f"⚠️ 处理异常：{e}", ctx_token)
            except Exception as e:
                logger.debug("WeChat error notification failed: %s", e)
            
    monitor = weclaw_bot.Monitor(client, handler, str(sync_buf_file))
    await monitor.run()
