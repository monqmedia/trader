"""Avisos y órdenes por ntfy (app gratuita, sin cuenta). El nombre del tema actúa de contraseña."""
import json
import time

import requests

from . import config as C

TITLE = "Inversor"


def enabled():
    return bool(C.NTFY_TOPIC)


def _url(suffix=""):
    return f"{C.NTFY_SERVER.rstrip('/')}/{C.NTFY_TOPIC}{suffix}"


def read_commands(state):
    if not enabled():
        return []
    st = state.setdefault("ntfy", {})
    since = st.get("since") or str(int(time.time()) - 60)
    try:
        r = requests.get(_url("/json"), params={"poll": "1", "since": since}, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"[ntfy] no se pudo leer: {e}")
        return []
    cmds = []
    for line in r.text.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("event") != "message":
            continue
        st["since"] = m["id"]
        text = (m.get("message") or "").strip().lower()
        if m.get("title") != TITLE and text.startswith("/"):
            cmds.append(text)
    if not st.get("welcomed"):
        st["welcomed"] = True
        st["new_owner"] = True
    return cmds


def send(state, text, quiet=False):
    if not enabled():
        return
    headers = {"Title": TITLE}
    if quiet:
        headers["Priority"] = "low"  # aparece en la lista sin sonar ni vibrar
    try:
        requests.post(_url(), data=text[:4000].encode("utf-8"),
                      headers=headers, timeout=20).raise_for_status()
    except Exception as e:
        print(f"[ntfy] no se pudo enviar: {e}")
