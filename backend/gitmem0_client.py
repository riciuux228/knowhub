"""gitmem0 v0.4.1 daemon 异步 TCP 客户端

gitmem0 是纯本地 agent 记忆系统，daemon 在 127.0.0.1:19840 监听。
通信协议：单条 JSON + \\n 换行。
支持动作：remember, search, query, extract, stats, metrics
"""
import asyncio
import json
import logging

logger = logging.getLogger("knowhub")

GITMEM0_HOST = "127.0.0.1"
GITMEM0_PORT = 19840


def _send_sync(action: str, timeout: float = 8.0, **params) -> dict:
    """同步发送单条请求到 gitmem0 daemon（在线程池中运行）"""
    import socket
    params["action"] = action
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((GITMEM0_HOST, GITMEM0_PORT))
        s.sendall((json.dumps(params, ensure_ascii=False) + "\n").encode())
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break
        s.close()
        return json.loads(data.decode())
    except Exception as e:
        print(f"[gitmem0] daemon 通信失败 ({action}): {type(e).__name__}: {e}", flush=True)
        return {"ok": False, "error": str(e)}

async def _send(action: str, timeout: float = 15.0, **params) -> dict:
    """异步包装：在线程池中运行同步 socket 调用"""
    return await asyncio.to_thread(_send_sync, action, timeout=timeout, **params)


async def remember(content: str, type: str = "fact", importance: float = 0.5,
                    source: str = "knowhub", tags: list = None) -> dict:
    """存储一条记忆"""
    return await _send("remember", content=content, type=type,
                       importance=importance, source=source, tags=tags or [])


async def search(query: str, top: int = 5) -> list:
    """多信号检索记忆，返回结果列表"""
    resp = await _send("search", query=query, top=top)
    return resp.get("data", []) if resp.get("ok") else []


async def query_context(message: str, budget: int = 1500) -> dict:
    """构建 LLM 注入上下文（自动检索 + 压缩 + Lost-in-the-Middle 排序）"""
    resp = await _send("query", message=message, budget=budget)
    return resp.get("data", {}) if resp.get("ok") else {}


async def extract(text: str, source: str = "knowhub") -> dict:
    """从文本中自动提取并存储多条记忆"""
    resp = await _send("extract", text=text, source=source)
    return resp.get("data", {}) if resp.get("ok") else {}


async def is_available() -> bool:
    """检查 gitmem0 daemon 是否在线"""
    resp = await _send("stats", timeout=5.0)
    return resp.get("ok", False)

async def warmup() -> bool:
    """预热 daemon（加载 embedding 模型），返回是否就绪"""
    try:
        # 用真实查询触发 embedding 模型加载（stats 不会加载模型）
        resp = await _send("query", message="warmup", budget=100, timeout=20.0)
        return resp.get("ok", False)
    except Exception as e:
        logger.debug("gitmem0 warmup failed: %s", e)
        return False


async def stats() -> dict:
    """获取记忆库统计信息"""
    resp = await _send("stats")
    return resp.get("data", {}) if resp.get("ok") else {}


async def metrics(reset: bool = False) -> dict:
    """获取 daemon 性能指标（调用次数、延迟等）"""
    params = {"reset": True} if reset else {}
    resp = await _send("metrics", **params)
    return resp.get("data", {}) if resp.get("ok") else {}
