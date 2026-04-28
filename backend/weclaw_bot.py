import os
import sys
import json
import base64
import random
import asyncio
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Any, Callable, Optional, List

import httpx

# ==========================================
# WeClaw Native Python Engine (replaces openilink and WeClaw Golang daemon)
# ==========================================

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"

# Message types
MSG_TYPE_NONE = 0
MSG_TYPE_USER = 1
MSG_TYPE_BOT = 2

# Message states
MSG_STATE_NEW = 0
MSG_STATE_GENERATING = 1
MSG_STATE_FINISH = 2

# Item types
ITEM_TYPE_NONE = 0
ITEM_TYPE_TEXT = 1
ITEM_TYPE_IMAGE = 2
ITEM_TYPE_VOICE = 3
ITEM_TYPE_FILE = 4
ITEM_TYPE_VIDEO = 5

# CDN Media Types
CDN_MEDIA_TYPE_IMAGE = 1
CDN_MEDIA_TYPE_VIDEO = 2
CDN_MEDIA_TYPE_FILE  = 3

# Typing Status
TYPING_STATUS_TYPING = 1
TYPING_STATUS_CANCEL = 2

class ILinkClient:
    """Native Python implementation of the WeClaw ILink HTTP client."""
    def __init__(self, bot_token: str = "", bot_id: str = "", base_url: str = DEFAULT_BASE_URL):
        self.bot_token = bot_token
        self.bot_id = bot_id
        self.base_url = base_url.rstrip("/") if base_url else DEFAULT_BASE_URL
        # generate uint32 wechat_uin
        n = random.randint(0, 0xFFFFFFFF)
        self.wechat_uin = base64.b64encode(str(n).encode('utf-8')).decode('utf-8')
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0))
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @property
    def headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.bot_token:
            h["AuthorizationType"] = "ilink_bot_token"
            h["Authorization"] = f"Bearer {self.bot_token}"
            h["X-WECHAT-UIN"] = self.wechat_uin
        return h

    async def get_bot_qrcode(self) -> Dict[str, Any]:
        resp = await self._get_client().get(f"{self.base_url}/ilink/bot/get_bot_qrcode?bot_type=3", headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    async def get_qrcode_status(self, qrcode: str) -> Dict[str, Any]:
        resp = await self._get_client().get(f"{self.base_url}/ilink/bot/get_qrcode_status?qrcode={qrcode}", headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    async def get_updates(self, get_updates_buf: str) -> Dict[str, Any]:
        req = {
            "get_updates_buf": get_updates_buf,
            "base_info": {"channel_version": "1.0.0"}
        }
        resp = await self._get_client().post(f"{self.base_url}/ilink/bot/getupdates", json=req, headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    async def send_message(self, msg_payload: dict) -> Dict[str, Any]:
        req = {
            "msg": msg_payload,
            "base_info": {"channel_version": "1.0.0"}
        }
        resp = await self._get_client().post(f"{self.base_url}/ilink/bot/sendmessage", json=req, headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    async def get_upload_url(self, filekey: str, media_type: int, to_user_id: str, raw_size: int, raw_md5: str, file_size: int, aes_key_hex: str) -> Dict[str, Any]:
        req = {
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": raw_size,
            "rawfilemd5": raw_md5,
            "filesize": file_size,
            "no_need_thumb": True,
            "aeskey": aes_key_hex,
            "base_info": {"channel_version": "1.0.0"}
        }
        resp = await self._get_client().post(f"{self.base_url}/ilink/bot/getuploadurl", json=req, headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    async def get_config(self, user_id: str, context_token: str) -> Dict[str, Any]:
        req = {
            "ilink_user_id": user_id,
            "context_token": context_token,
            "base_info": {"channel_version": "1.0.0"}
        }
        resp = await self._get_client().post(f"{self.base_url}/ilink/bot/getconfig", json=req, headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    async def send_typing(self, user_id: str, typing_ticket: str, status: int) -> Dict[str, Any]:
        req = {
            "ilink_user_id": user_id,
            "typing_ticket": typing_ticket,
            "status": status,
            "base_info": {"channel_version": "1.0.0"}
        }
        resp = await self._get_client().post(f"{self.base_url}/ilink/bot/sendtyping", json=req, headers=self.headers)
        resp.raise_for_status()
        return resp.json()

# Helper API functions for High Level Usage
async def print_qr_and_poll_login():
    """Fetches QR code, prints to console, and polls until confirmed."""
    import qrcode
    import io
    print("Fetching QR code...", flush=True)
    client = ILinkClient()
    qr_data = await client.get_bot_qrcode()
    qr_url = qr_data.get("qrcode_img_content", "")
    qrcode_id = qr_data.get("qrcode", "")
    
    if not qr_url:
        raise Exception("Failed to get QR code URL")
    
    # Print QR to console using terminal blocks
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=1, border=1)
    qr.add_data(qr_url)
    qr.make(fit=True)
    # qr.print_ascii(tty=True) # 容易在 Windows 终端中被误认为进度条刷屏
    print(f"\n👉 请按住 Ctrl + 鼠标点击 下方链接，在浏览器中扫码登录：\n")
    print(f"🔗 {qr_url}\n")
    print("Waiting for scan...", flush=True)
    
    last_status = ""
    while True:
        try:
            status_data = await client.get_qrcode_status(qrcode_id)
        except Exception as e:
            await asyncio.sleep(2)
            continue
            
        status = status_data.get("status", "")
        if status != last_status:
            last_status = status
            if status == "scaned":
                print("QR code scanned! Please confirm on your phone.", flush=True)
            elif status == "confirmed":
                print("Login confirmed!", flush=True)
                return {
                    "bot_token": status_data.get("bot_token"),
                    "ilink_bot_id": status_data.get("ilink_bot_id"),
                    "base_url": status_data.get("baseurl"),
                    "ilink_user_id": status_data.get("ilink_user_id")
                }
            elif status == "expired":
                raise Exception("QR code expired.")
        await asyncio.sleep(1)

class Monitor:
    """Manages the long-poll loop mirroring weclaw/ilink/monitor.go"""
    def __init__(self, client: ILinkClient, handler_coroutine, sync_buf_file: str):
        self.client = client
        self.handler = handler_coroutine
        self.sync_buf_file = sync_buf_file
        self.get_updates_buf = ""
        self.failures = 0
        self._load_buf()
        
    def _load_buf(self):
        if os.path.exists(self.sync_buf_file):
            try:
                with open(self.sync_buf_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.get_updates_buf = data.get("get_updates_buf", "")
                    print(f"[Monitor] Loaded sync buf from {self.sync_buf_file}", flush=True)
            except Exception as e:
                print(f"[Monitor] Failed to load sync buf: {e}", flush=True)
                
    def _save_buf(self):
        try:
            os.makedirs(os.path.dirname(self.sync_buf_file), exist_ok=True)
            with open(self.sync_buf_file, 'w', encoding='utf-8') as f:
                json.dump({"get_updates_buf": self.get_updates_buf}, f)
        except Exception as e:
            print(f"[Monitor] Failed to save sync buf: {e}", flush=True)

    def _calc_backoff(self) -> float:
        return min(60.0, 3.0 * (2 ** max(0, self.failures - 1)))
        
    async def run(self):
        print("[Monitor] Starting long-poll loop", flush=True)
        while True:
            try:
                resp = await self.client.get_updates(self.get_updates_buf)
            except Exception as e:
                self.failures += 1
                backoff = self._calc_backoff()
                print(f"[Monitor] GetUpdates error ({self.failures}/5, backoff={backoff}s): {e}", flush=True)
                if self.failures >= 5:
                    print("[Monitor] WARNING: 5 consecutive failures. If this persists, delete token and re-login.", flush=True)
                await asyncio.sleep(backoff)
                continue
                
            self.failures = 0
            
            err_code = resp.get("errcode", 0)
            if err_code == -14: # errCodeSessionExpired
                if self.get_updates_buf:
                    print("[Monitor] Session expired, resetting sync buf", flush=True)
                    self.get_updates_buf = ""
                    self._save_buf()
                else:
                    print("[Monitor] WARNING: WeChat session expired totally. Please delete token and re-login.", flush=True)
                await asyncio.sleep(5)
                continue
                
            ret = resp.get("ret", 0)
            if ret != 0 and err_code != 0:
                print(f"[Monitor] Server error: ret={ret} errcode={err_code} errmsg={resp.get('errmsg')}", flush=True)
                await asyncio.sleep(1)
                continue
                
            new_buf = resp.get("get_updates_buf", "")
            if new_buf:
                self.get_updates_buf = new_buf
                self._save_buf()
                
            msgs = resp.get("msgs", [])
            for msg in msgs:
                # Fire and forget concurrent handling with error logging
                async def _safe_handler(m):
                    try:
                        await self.handler(self.client, m)
                    except Exception as e:
                        print(f"[Monitor] Handler error: {e}", flush=True)
                asyncio.create_task(_safe_handler(msg))

# ==========================================
# Extraction Helpers for WeChat Messages
# ==========================================

def extract_text(msg: dict) -> str:
    for item in msg.get("item_list", []):
        if item.get("type") == ITEM_TYPE_TEXT and "text_item" in item:
            return item["text_item"].get("text", "")
    return ""

def extract_voice_text(msg: dict) -> str:
    for item in msg.get("item_list", []):
        if item.get("type") == ITEM_TYPE_VOICE and "voice_item" in item:
            return item["voice_item"].get("text", "")
    return ""

def extract_image(msg: dict) -> Optional[dict]:
    """Returns the image_item dict if an image exists."""
    for item in msg.get("item_list", []):
        if item.get("type") == ITEM_TYPE_IMAGE and "image_item" in item:
            return item["image_item"]
    return None

def extract_file(msg: dict) -> Optional[dict]:
    """Returns the file_item dict if a file exists."""
    for item in msg.get("item_list", []):
        if item.get("type") == ITEM_TYPE_FILE and "file_item" in item:
            return item["file_item"]
    return None

def build_text_message(to_user_id: str, text: str, context_token: str, client_id: str) -> dict:
    return {
        "from_user_id": "", # filled by iLink automatically or leave empty
        "to_user_id": to_user_id,
        "client_id": client_id,
        "message_type": MSG_TYPE_BOT,
        "message_state": MSG_STATE_FINISH,
        "context_token": context_token,
        "item_list": [{
            "type": ITEM_TYPE_TEXT,
            "text_item": {"text": text}
        }]
    }

def build_image_message(to_user_id: str, cdn_media_info: dict, hd_size: int, context_token: str, client_id: str) -> dict:
    return {
        "from_user_id": "",
        "to_user_id": to_user_id,
        "client_id": client_id,
        "message_type": MSG_TYPE_BOT,
        "message_state": MSG_STATE_FINISH,
        "context_token": context_token,
        "item_list": [{
            "type": ITEM_TYPE_IMAGE,
            "image_item": {
                "media": cdn_media_info,
                "mid_size": hd_size
            }
        }]
    }

def build_file_message(to_user_id: str, cdn_media_info: dict, file_name: str, file_len_str: str, context_token: str, client_id: str) -> dict:
    return {
        "from_user_id": "",
        "to_user_id": to_user_id,
        "client_id": client_id,
        "message_type": MSG_TYPE_BOT,
        "message_state": MSG_STATE_FINISH,
        "context_token": context_token,
        "item_list": [{
            "type": ITEM_TYPE_FILE,
            "file_item": {
                "media": cdn_media_info,
                "file_name": file_name,
                "len": file_len_str
            }
        }]
    }
