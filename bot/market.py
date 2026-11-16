"""Busca meme coins, mide su fuerza y descarta las peligrosas."""
import time

from . import config as C
from .net import get_json

GECKO = "https://api.geckoterminal.com/api/v2"
DEX = "https://api.dexscreener.com"
RUGCHECK = "https://api.rugcheck.xyz/v1"


def _num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


# ---------- 1. Fuentes de candidatas ----------

def gecko_pools(path, params=None):
    data = get_json(GECKO + path, params, headers={"Accept": "application/json;version=20230302"})
    out = []
    for p in data.get("data") or []:
        a = p.get("attributes") or {}
        bid = (((p.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id", "")
        if "_" not in bid:
            continue
        h1 = (a.get("transactions") or {}).get("h1") or {}
        h24 = (a.get("transactions") or {}).get("h24") or {}
        out.append({
            "mint": bid.split("_", 1)[1],
            "buyers_h1": h1.get("buyers"),
            "sellers_h1": h1.get("sellers"),
            "buyers_h24": h24.get("buyers"),
            "sellers_h24": h24.get("sellers"),
        })
    return out


def discover():
    """Devuelve {mint: info_extra} de varias listas de tendencia."""
    found = {}
    sources = [
        ("tendencia_1h", "/networks/solana/trending_pools", {"duration": "1h", "include": "base_token"}),
        ("tendencia_6h", "/networks/solana/trending_pools", {"duration": "6h", "include": "base_token"}),
        ("mas_volumen", "/networks/solana/pools", {"sort": "h24_volume_usd_desc", "include": "base_token"}),
    ]
    for name, path, params in sources:
        try:
            pools = gecko_pools(path, params)
        except Exception as e:  # una fuente caída no para el resto
            print(f"[mercado] {name} falló: {e}")
            continue
        for rank, p in enumerate(pools, 1):
            m = p["mint"]
            if m in C.EXCLUDED_MINTS:
                continue
            entry = found.setdefault(m, {"sources": []})
            entry["sources"].append(f"{name}#{rank}")
            for k in ("buyers_h1", "sellers_h1", "buyers_h24", "sellers_h24"):
                if entry.get(k) is None and p.get(k) is not None:
                    entry[k] = p[k]
        time.sleep(2.5)  # límite gratuito de GeckoTerminal
    try:
        for b in get_json(DEX + "/token-boosts/top/v1") or []:
            if b.get("chainId") == "solana" and b.get("tokenAddress") not in C.EXCLUDED_MINTS:
                found.setdefault(b["tokenAddress"], {"sources": []})["sources"].append("promocionada")
    except Exception as e:
        print(f"[mercado] dexscreener boosts falló: {e}")
    return found


# ---------- 2. Datos de mercado ----------

def dex_tokens(mints):
    """Datos del par con más liquidez de cada moneda. {mint: token}"""
    out = {}
    mints = list(dict.fromkeys(mints))
    for i in range(0, len(mints), 30):
        chunk = mints[i:i + 30]
        try:
            pairs = get_json(f"{DEX}/tokens/v1/solana/{','.join(chunk)}") or []
        except Exception as e:
            print(f"[mercado] dexscreener tokens falló: {e}")
            continue
        if isinstance(pairs, dict):
            pairs = pairs.get("pairs") or []
        for p in pairs:
            m = (p.get("baseToken") or {}).get("address")
            if m not in chunk:
                continue
            liq = _num((p.get("liquidity") or {}).get("usd"))
            if m in out and out[m]["liq"] >= liq:
                continue
            out[m] = parse_pair(p)
    return out


def parse_pair(p):
    vol = p.get("volume") or {}
    chg = p.get("priceChange") or {}
    tx = p.get("txns") or {}
    info = p.get("info") or {}
    created = p.get("pairCreatedAt")
    age_h = (time.time() - created / 1000) / 3600 if created else None
    return {
        "mint": p["baseToken"]["address"],
        "symbol": p["baseToken"].get("symbol", "?"),
        "name": p["baseToken"].get("name", "?"),
        "price": _num(p.get("priceUsd")),
        "mcap": _num(p.get("marketCap") or p.get("fdv")),
        "liq": _num((p.get("liquidity") or {}).get("usd")),
        "vol": {k: _num(vol.get(k)) for k in ("m5", "h1", "h6", "h24")},
        "chg": {k: _num(chg.get(k)) for k in ("m5", "h1", "h6", "h24")},
        "txns_h1": (tx.get("h1") or {}),
        "txns_h24": (tx.get("h24") or {}),
        "age_h": round(age_h, 1) if age_h is not None else None,
        "socials": len(info.get("socials") or []) + len(info.get("websites") or []),
        "boosted": bool((p.get("boosts") or {}).get("active")),
        "dex": p.get("dexId"),
    }


# ---------- 3. Filtros ----------

def tier(t):
    """'joven', 'consolidada' o None si no cumple los mínimos."""
    mc, liq, v24, age = t["mcap"], t["liq"], t["vol"]["h24"], t["age_h"]
    if C.YOUNG_MCAP[0] <= mc < C.YOUNG_MCAP[1]:
        if liq >= C.YOUNG_MIN_LIQ and v24 >= C.YOUNG_MIN_VOL24 and age is not None and age >= C.MIN_AGE_H:
            return "joven"
    elif C.ESTABLISHED_MCAP[0] <= mc <= C.ESTABLISHED_MCAP[1]:
        if liq >= C.ESTABLISHED_MIN_LIQ and v24 >= C.ESTABLISHED_MIN_VOL24:
            return "consolidada"
    return None


def momentum_score(t):
    """Ordena candidatas antes de la revisión de seguridad (más es mejor)."""
    turnover = t["vol"]["h1"] / max(t["mcap"], 1)
    b, s = _num(t["txns_h1"].get("buys")), _num(t["txns_h1"].get("sells"))
    pressure = b / max(b + s, 1)
    trend = max(min(t["chg"]["h1"], 100), -50) / 100
    return turnover * 10 + pressure + trend


def vet(mint, tier_name):
    """Revisión de seguridad con RugCheck. Devuelve (ok, problemas, avisos, datos)."""
    try:
        r = get_json(f"{RUGCHECK}/tokens/{mint}/report", tries=2)
    except Exception as e:
        return False, [f"no se pudo verificar ({e})"], [], {}
    return judge_report(r, tier_name)


def judge_report(r, tier_name):
    problems, warns = [], []
    if r.get("rugged"):
        problems.append("ya fue una estafa (rug)")
    if r.get("mintAuthority"):
        problems.append("el creador puede fabricar más monedas")
    if r.get("freezeAuthority"):
        problems.append("el creador puede congelar monedas")
    if _num((r.get("transferFee") or {}).get("pct")) > 0:
        problems.append("cobra comisión al mover la moneda")

    known = set((r.get("knownAccounts") or {}).keys())
    holders = [h for h in (r.get("topHolders") or [])
               if h.get("address") not in known and h.get("owner") not in known]
    pcts = [_num(h.get("pct")) for h in holders[:10]]
    top10 = sum(pcts)
    if pcts and max(pcts) <= 1 and top10 <= 1:  # viene en fracción, no en %
        top10 *= 100
    if top10 > C.MAX_TOP10_PCT:
        problems.append(f"10 cuentas tienen el {top10:.0f}%")
    if r.get("graphInsidersDetected"):
        warns.append("posibles cuentas conectadas al creador")

    for risk in r.get("risks") or []:
        name = risk.get("name", "?")
        lvl = risk.get("level")
        lp_related = "lp" in name.lower() or "liquidity" in name.lower()
        if lvl == "danger" and not (lp_related and tier_name == "consolidada"):
            problems.append(name)
        elif lvl in ("danger", "warn"):
            warns.append(name)

    data = {"top10_pct": round(top10, 1), "holders": r.get("totalHolders"),
            "rugcheck_score": r.get("score_normalised")}
    return not problems, problems, warns, data


def find_candidates(held, cooldown):
    """Lista final de candidatas verificadas, lista para la IA."""
    pool = discover()
    skip = set(held) | set(cooldown)
    tokens = dex_tokens([m for m in pool if m not in skip])
    ranked = []
    for m, t in tokens.items():
        tn = tier(t)
        if tn:
            t["tier"] = tn
            t.update({k: v for k, v in pool[m].items()})
            ranked.append(t)
    ranked.sort(key=momentum_score, reverse=True)
    print(f"[mercado] {len(pool)} vistas, {len(ranked)} pasan filtros de mercado")

    out = []
    for t in ranked[:C.MAX_CANDIDATES_TO_VET]:
        ok, problems, warns, data = vet(t["mint"], t["tier"])
        time.sleep(1)
        if not ok:
            print(f"[seguridad] descartada {t['symbol']}: {', '.join(problems)}")
            continue
        t["warnings"] = warns
        t.update(data)
        out.append(t)
    print(f"[mercado] {len(out)} candidatas pasan la seguridad")
    return out


def sol_change_24h():
    try:
        t = dex_tokens([C.SOL_MINT]).get(C.SOL_MINT)
        return t["chg"]["h24"] if t else None
    except Exception:
        return None
