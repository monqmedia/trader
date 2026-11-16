"""Reglas de cartera (sin red): tramos de venta, validación de órdenes de la IA, tamaños."""
from . import config as C


def clamp(x, lo, hi):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, x))


def price_multiple(pos, amount_now, value_now_usd):
    """Cuántas veces se ha multiplicado el precio desde la compra."""
    if amount_now <= 0 or pos.get("amount_initial", 0) <= 0 or pos.get("cost_usd", 0) <= 0:
        return 0.0
    entry_unit = pos["cost_usd"] / pos["amount_initial"]
    return (value_now_usd / amount_now) / entry_unit


def take_profit_due(pos, multiple):
    """Devuelve (tramo, fracción de la cantidad original) si toca vender por beneficio."""
    stage = pos.get("tp_stage", 0)
    if stage < len(C.TAKE_PROFITS) and multiple >= C.TAKE_PROFITS[stage][0]:
        return stage, C.TAKE_PROFITS[stage][1]
    return None


def standard_buy_usd(equity_usd, cash_usd, sol_usd):
    """Tamaño estándar: capital total repartido en N apuestas (reinvierte ganancias),
    limitado por el efectivo disponible menos la reserva para comisiones."""
    available = cash_usd - C.SOL_RESERVE * sol_usd
    return max(0.0, min(equity_usd / C.MAX_POSITIONS, available))


def validate(actions, positions, candidates, buys_left):
    """Filtra las órdenes de la IA. Solo vende lo que tiene y solo compra candidatas verificadas."""
    cand = {c["mint"]: c for c in candidates}
    sells, buys, rejected = [], [], []
    for a in actions or []:
        if a.get("type") == "sell":
            f = clamp(a.get("sell_fraction"), 0, 1)
            if a.get("mint") in positions and f >= 0.05:
                sells.append({**a, "sell_fraction": 1.0 if f >= 0.95 else f})
            else:
                rejected.append((a, "venta no válida"))
    full_exits = sum(1 for s in sells if s["sell_fraction"] == 1.0)
    slots = C.MAX_POSITIONS - len(positions) + full_exits
    seen = set()
    for a in actions or []:
        if a.get("type") != "buy":
            continue
        m = a.get("mint")
        if m not in cand:
            rejected.append((a, "no está en la lista verificada"))
        elif m in positions or m in seen:
            rejected.append((a, "ya se tiene"))
        elif slots <= 0 or buys_left <= 0:
            rejected.append((a, "sin hueco o límite diario"))
        else:
            buys.append({**a, "size_fraction": clamp(a.get("size_fraction"), 0.5, 1.0)})
            seen.add(m)
            slots -= 1
            buys_left -= 1
    return sells, buys, rejected


def price_signal(hist, now, multiple):
    """Mira el historial reciente [(t, multiplicador)] y devuelve 'crash', 'alert_down', 'alert_up' o None."""
    if multiple <= 0:  # sin ruta de venta o valor cero: posible estafa en curso
        return "crash" if any(m > 0 for _, m in hist) else None
    recent = [m for t, m in hist if now - t <= C.CRASH_WINDOW_MIN * 60 and m > 0]
    if recent and 1 - multiple / max(recent) >= C.CRASH_DROP:
        return "crash"
    older = [m for t, m in hist if now - t <= C.ALERT_WINDOW_MIN * 60 and m > 0]
    if older:
        change = multiple / older[0] - 1
        if change <= -C.ALERT_MOVE:
            return "alert_down"
        if change >= C.ALERT_MOVE:
            return "alert_up"
    return None


def trailing_exit_due(pos, multiple):
    """Tras la primera venta con beneficio, protege lo ganado si el precio se desploma desde su máximo."""
    return (pos.get("tp_stage", 0) >= 1 and multiple > 0
            and multiple <= pos.get("peak_multiple", 0) * (1 - C.TRAIL_DROP))
