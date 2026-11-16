"""Avisos y órdenes por Telegram."""
from . import config as C
from .net import get_json, post_json

API = "https://api.telegram.org/bot{token}/{method}"


def _url(method):
    return API.format(token=C.TELEGRAM_BOT_TOKEN, method=method)


def enabled():
    return bool(C.TELEGRAM_BOT_TOKEN)


def read_commands(state):
    """Lee mensajes nuevos. Fija el dueño (primer chat que escribe) y devuelve sus órdenes."""
    if not enabled():
        return []
    tg = state.setdefault("telegram", {})
    if C.TELEGRAM_CHAT_ID:
        tg["chat_id"] = C.TELEGRAM_CHAT_ID
    try:
        r = get_json(_url("getUpdates"), {"offset": tg.get("offset", 0), "timeout": 0})
    except Exception as e:
        print(f"[telegram] no se pudo leer: {e}")
        return []
    cmds = []
    for u in r.get("result") or []:
        tg["offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        if not chat:
            continue
        if not tg.get("chat_id"):
            tg["chat_id"] = chat
            tg["new_owner"] = True
        if chat == str(tg["chat_id"]):
            text = (msg.get("text") or "").strip().lower().split("@")[0]
            if text.startswith("/"):
                cmds.append(text)
    return cmds


def send(state, text):
    print("[aviso]", text.replace("\n", " | "))
    chat = (state.get("telegram") or {}).get("chat_id")
    if not enabled() or not chat:
        return
    try:
        post_json(_url("sendMessage"), {"chat_id": chat, "text": text[:4000],
                                        "disable_web_page_preview": True})
    except Exception as e:
        print(f"[telegram] no se pudo enviar: {e}")
