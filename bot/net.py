import time

import requests

_session = requests.Session()
_session.headers["User-Agent"] = "trader-bot/1.0"


def get_json(url, params=None, headers=None, tries=3):
    return _request("GET", url, params=params, headers=headers, tries=tries)


def post_json(url, body, headers=None, tries=3):
    return _request("POST", url, json=body, headers=headers, tries=tries)


def _request(method, url, tries=3, **kw):
    last = None
    for i in range(tries):
        try:
            r = _session.request(method, url, timeout=25, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                last = RuntimeError(f"{url} -> HTTP {r.status_code}")
                time.sleep(2 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last
