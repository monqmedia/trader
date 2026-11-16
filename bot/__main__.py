"""Una ronda del inversor: mirar, pensar, actuar, avisar. GitHub la lanza cada 30 minutos."""
import json
import os
import sys
import time
from datetime import datetime, timezone

from . import brain, market, telegram
from . import config as C
from . import portfolio as P
from .solana import Jupiter, PaperWallet, RealWallet, Rpc, sol_price_usd, value_in_sol

HELP = ("Órdenes:\n/estado – cómo va todo\n/pausa – parar de operar\n"
        "/seguir – volver a operar\n/vender_todo – vender todo y pausar")


# ---------- estado ----------

def load_state():
    try:
        with open(C.STATE_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(C.STATE_PATH), exist_ok=True)
    tmp = C.STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1, ensure_ascii=False)
    os.replace(tmp, C.STATE_PATH)


def usd(x):
    return f"${x:,.2f}"


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class Round:
    def __init__(self, state):
        self.s = state
        self.now = time.time()
        self.jup = Jupiter()
        self.sol_usd = sol_price_usd(self.jup)
        if C.MODE == "real":
            if not C.SOLANA_PRIVATE_KEY:
                raise SystemExit("Falta SOLANA_PRIVATE_KEY para operar en real.")
            self.wallet = RealWallet(C.SOLANA_PRIVATE_KEY, Rpc(C.SOLANA_RPC_URL), self.jup)
        else:
            self.wallet = PaperWallet(state, self.jup, self.sol_usd)
        for k, v in (("positions", {}), ("closed", []), ("journal", []), ("buys", []),
                     ("cooldown", {}), ("ai", {}), ("history", [])):
            state.setdefault(k, v)

    # ---- utilidades ----
    def notify(self, text):
        prefix = "🧪 [SIMULADO] " if C.MODE != "real" else ""
        telegram.send(self.s, prefix + text)

    def refresh(self):
        """Lee saldos, valora cada posición y cierra las que ya no están en la cuenta."""
        self.cash_lamports = self.wallet.sol_lamports()
        self.balances = self.wallet.token_balances()
        self.values = {}
        for mint in list(self.s["positions"]):
            amt = self.balances.get(mint, 0)
            pos = self.s["positions"][mint]
            if amt <= 0:
                if self.now - pos["opened_at"] > 600:  # recién comprada: la red puede ir con retraso
                    self.close(mint, "ya no está en la cuenta")
                continue
            v = value_in_sol(self.jup, mint, amt) / 1e9 * self.sol_usd
            self.values[mint] = v
            pos = self.s["positions"][mint]
            pos["multiple"] = round(P.price_multiple(pos, amt, v), 3)
            pos["peak_multiple"] = max(pos.get("peak_multiple", 1.0), pos["multiple"])
        self.cash_usd = self.cash_lamports / 1e9 * self.sol_usd
        self.equity = self.cash_usd + sum(self.values.values())

    def close(self, mint, reason):
        pos = self.s["positions"].pop(mint)
        ret = pos.get("realized_usd", 0.0)
        mult = ret / pos["cost_usd"] if pos.get("cost_usd") else 0
        self.s["closed"].append({
            "symbol": pos["symbol"], "mint": mint, "tier": pos.get("tier"),
            "cost_usd": round(pos["cost_usd"], 2), "returned_usd": round(ret, 2),
            "result_multiple": round(mult, 2), "peak_multiple": round(pos.get("peak_multiple", 1), 2),
            "opened": iso(pos["opened_at"]), "closed": iso(self.now),
            "entry_reason": pos.get("entry_reason", ""), "exit_reason": reason,
        })
        self.s["closed"] = self.s["closed"][-50:]
        self.s["cooldown"][mint] = self.now
        sign = "🟢" if mult >= 1 else "🔴"
        self.notify(f"{sign} Cerrada {pos['symbol']}: invertido {usd(pos['cost_usd'])}, "
                    f"recuperado {usd(ret)} (x{mult:.2f}).\nMotivo: {reason}")

    # ---- compraventa ----
    def buy(self, cand, buy_usd, reason):
        lamports = int(buy_usd / self.sol_usd * 1e9)
        try:
            q = self.jup.quote(C.SOL_MINT, cand["mint"], lamports, C.BUY_SLIPPAGE_BPS)
            back = int(self.jup.quote(cand["mint"], C.SOL_MINT, int(q["outAmount"]),
                                      C.SELL_SLIPPAGE_BPS)["outAmount"])
        except Exception as e:
            print(f"[compra] {cand['symbol']} sin ruta: {e}")
            return False
        loss = 1 - back / lamports
        if loss > C.MAX_ROUNDTRIP_LOSS:
            print(f"[compra] {cand['symbol']} descartada: comprar y vender perdería {loss:.0%}")
            return False
        res = self.wallet.swap(q)
        if not res.get("ok"):
            self.notify(f"⚠️ Falló la compra de {cand['symbol']}: {res.get('error')}")
            return False
        cost = res["in_amount"] / 1e9 * self.sol_usd
        self.s["positions"][cand["mint"]] = {
            "symbol": cand["symbol"], "name": cand.get("name"), "tier": cand.get("tier"),
            "cost_usd": cost, "amount_initial": res["out_amount"], "opened_at": self.now,
            "tp_stage": 0, "realized_usd": 0.0, "peak_multiple": 1.0, "entry_reason": reason,
        }
        self.s["buys"].append(self.now)
        self.notify(f"🛒 Compra {cand['symbol']} ({cand.get('tier')}) por {usd(cost)}.\n"
                    f"Valor de mercado {usd(cand.get('mcap', 0))}.\nPor qué: {reason}\n"
                    f"https://dexscreener.com/solana/{cand['mint']}")
        return True

    def sell(self, mint, amount, reason):
        pos = self.s["positions"][mint]
        res = None
        for slip in (C.SELL_SLIPPAGE_BPS, C.PANIC_SLIPPAGE_BPS):
            try:
                q = self.jup.quote(mint, C.SOL_MINT, amount, slip)
            except Exception as e:
                res = {"ok": False, "error": str(e)}
                continue
            res = self.wallet.swap(q)
            if res.get("ok"):
                break
        if not res or not res.get("ok"):
            self.notify(f"⚠️ No se pudo vender {pos['symbol']}: {(res or {}).get('error')}")
            return False
        got = res["out_amount"] / 1e9 * self.sol_usd
        pos["realized_usd"] = pos.get("realized_usd", 0.0) + got
        left = self.wallet.token_balances().get(mint, 0)
        if left <= 0 or (left < amount * 0.02):
            self.close(mint, reason)
        else:
            self.notify(f"💰 Venta parcial {pos['symbol']}: {usd(got)}.\nMotivo: {reason}")
        return True

    # ---- partes de la ronda ----
    def take_profits(self):
        for mint, pos in list(self.s["positions"].items()):
            due = P.take_profit_due(pos, pos.get("multiple", 0))
            if not due:
                continue
            stage, frac = due
            amt = min(int(pos["amount_initial"] * frac), self.balances.get(mint, 0))
            mult = C.TAKE_PROFITS[stage][0]
            if amt > 0 and self.sell(mint, amt, f"subió x{mult:g}: asegurar beneficios"):
                if mint in self.s["positions"]:
                    pos["tp_stage"] = stage + 1

    def sell_all(self, reason):
        for mint in list(self.s["positions"]):
            amt = self.balances.get(mint, 0)
            if amt > 0:
                self.sell(mint, amt, reason)

    def status_text(self):
        start = self.s.get("start_equity_usd") or self.equity
        lines = [f"📊 Capital: {usd(self.equity)} ({(self.equity / start - 1) * 100:+.1f}% desde el inicio)",
                 f"Efectivo: {usd(self.cash_usd)}"]
        for mint, pos in self.s["positions"].items():
            lines.append(f"• {pos['symbol']}: {usd(self.values.get(mint, 0))} "
                         f"(x{pos.get('multiple', 0):.2f}, invertido {usd(pos['cost_usd'])})")
        ai = self.s["ai"]
        lines.append(f"Gasto IA este mes: {usd(ai.get('spent_month', 0))} de {usd(C.AI_MONTHLY_BUDGET_USD)}")
        if self.s.get("paused"):
            lines.append("⏸️ En pausa. Escribe /seguir para reanudar.")
        return "\n".join(lines)

    def handle_commands(self, cmds):
        for c in cmds:
            if c.startswith("/estado"):
                self.notify(self.status_text())
            elif c.startswith("/pausa"):
                self.s["paused"] = True
                self.notify("⏸️ Pausado. No compraré ni venderé hasta que escribas /seguir.")
            elif c.startswith("/seguir"):
                self.s["paused"] = False
                self.notify("▶️ Reanudado.")
            elif c.startswith("/vender_todo"):
                self.sell_all("lo pediste por Telegram")
                self.s["paused"] = True
                self.notify("🛑 Vendido todo y en pausa. Escribe /seguir para volver.")
            else:
                self.notify(HELP)

    def ai_budget_ok(self):
        ai = self.s["ai"]
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if ai.get("month") != month:
            ai.update(month=month, spent_month=0.0, budget_warned=False)
        if ai.get("day") != day:
            ai.update(day=day, spent_day=0.0)
        daily_cap = C.AI_MONTHLY_BUDGET_USD / 30 * 3
        ok = ai["spent_month"] < C.AI_MONTHLY_BUDGET_USD and ai["spent_day"] < daily_cap
        if not ok and not ai.get("budget_warned"):
            ai["budget_warned"] = True
            self.notify("💸 La IA llegó a su tope de gasto. Las ventas automáticas por beneficio siguen activas.")
        return ok

    def charge(self, cost):
        ai = self.s["ai"]
        ai["spent_month"] = round(ai.get("spent_month", 0) + cost, 4)
        ai["spent_day"] = round(ai.get("spent_day", 0) + cost, 4)

    def snapshot(self, candidates, held_market, buys_left, std_usd):
        def compact(t):
            if not t:
                return None
            keys = ("symbol", "name", "mint", "tier", "mcap", "liq", "age_h", "vol", "chg", "txns_h1",
                    "txns_h24", "buyers_h1", "sellers_h1", "buyers_h24", "sellers_h24", "socials",
                    "boosted", "sources", "warnings", "top10_pct", "holders")
            out = {k: t[k] for k in keys if t.get(k) not in (None, [], "")}
            for k in ("mcap", "liq"):
                if k in out:
                    out[k] = round(out[k])
            for k in ("vol", "chg"):
                if k in out:
                    out[k] = {a: round(b, 1) for a, b in out[k].items()}
            return out

        positions = []
        for mint, pos in self.s["positions"].items():
            positions.append({
                "mint": mint, "symbol": pos["symbol"], "tier": pos.get("tier"),
                "held_hours": round((self.now - pos["opened_at"]) / 3600, 1),
                "cost_usd": round(pos["cost_usd"], 2), "value_now_usd": round(self.values.get(mint, 0), 2),
                "realized_usd": round(pos.get("realized_usd", 0), 2), "multiple": pos.get("multiple"),
                "peak_multiple": round(pos.get("peak_multiple", 1), 2),
                "auto_take_profits_done": pos.get("tp_stage", 0), "entry_reason": pos.get("entry_reason"),
                "market": compact(held_market.get(mint)),
            })
        return {
            "now_utc": iso(self.now),
            "account": {"equity_usd": round(self.equity, 2), "cash_usd": round(self.cash_usd, 2),
                        "start_usd": round(self.s.get("start_equity_usd", self.equity), 2),
                        "free_slots": C.MAX_POSITIONS - len(self.s["positions"]),
                        "buys_left_today": buys_left, "standard_buy_usd": round(std_usd, 2)},
            "sol_change_24h_pct": market.sol_change_24h(),
            "positions": positions,
            "candidates": [compact(c) for c in candidates],
            "recent_journal": self.s["journal"][-5:],
            "closed_trades": self.s["closed"][-10:],
        }

    def think_and_act(self):
        held = list(self.s["positions"])
        self.s["cooldown"] = {m: t for m, t in self.s["cooldown"].items()
                              if self.now - t < C.REBUY_COOLDOWN_H * 3600}
        buys_left = C.MAX_BUYS_PER_DAY - sum(1 for t in self.s["buys"] if self.now - t < 86400)
        self.s["buys"] = [t for t in self.s["buys"] if self.now - t < 86400]
        std_usd = P.standard_buy_usd(self.equity, self.cash_usd, self.sol_usd)

        candidates = market.find_candidates(held, self.s["cooldown"])
        if not candidates and not held:
            print("[ronda] nada que vigilar ni comprar")
            return
        if not C.ANTHROPIC_API_KEY:
            print("[ronda] falta ANTHROPIC_API_KEY: la IA no puede decidir")
            return
        if not self.ai_budget_ok():
            return

        held_market = market.dex_tokens(held) if held else {}
        snap = self.snapshot(candidates, held_market, buys_left, std_usd)

        last = self.s["ai"].get("last_decider", 0)
        forced = self.now - last > C.DECIDER_MAX_INTERVAL_H * 3600
        if forced:
            why = "revisión periódica"
        else:
            try:
                w, cost = brain.watch(snap)
                self.charge(cost)
            except Exception as e:
                print(f"[ia] vigilante falló: {e}")
                return
            print(f"[vigilante] escalar={w['escalate']}: {w['reason']}")
            if not w["escalate"]:
                return
            why = w["reason"]

        try:
            d, cost = brain.decide(snap)
            self.charge(cost)
        except Exception as e:
            print(f"[ia] decisor falló: {e}")
            return
        self.s["ai"]["last_decider"] = self.now
        print(f"[decisor] ({why}) {d['market_view']}")

        sells, buys, rejected = P.validate(d.get("actions"), self.s["positions"], candidates, buys_left)
        for a, r in rejected:
            print(f"[decisor] orden rechazada ({r}): {a}")
        for a in sells:
            amt = self.balances.get(a["mint"], 0)
            if a["sell_fraction"] < 1:
                amt = int(amt * a["sell_fraction"])
            if amt > 0:
                self.sell(a["mint"], amt, a["reason"])
        if buys:
            self.refresh()
        cand = {c["mint"]: c for c in candidates}
        for a in buys:
            if len(self.s["positions"]) >= C.MAX_POSITIONS:
                break
            std = P.standard_buy_usd(self.equity, self.cash_usd, self.sol_usd)
            size = std * a["size_fraction"]
            if size < C.MIN_POSITION_USD:
                print(f"[compra] tamaño {usd(size)} demasiado pequeño")
                break
            if self.buy(cand[a["mint"]], size, a["reason"]):
                self.cash_usd -= size  # el capital total no cambia: efectivo -> moneda

        self.s["journal"].append({
            "t": iso(self.now), "market_view": d["market_view"],
            "actions": [f"{a['type']} {a['mint'][:6]}: {a['reason']}" for a in sells + buys],
            "notes": d.get("notes_for_next_time", ""),
        })
        self.s["journal"] = self.s["journal"][-30:]
        if not sells and not buys:
            print("[decisor] decidió esperar")

    def daily_summary(self):
        now = datetime.now(timezone.utc)
        day = now.strftime("%Y-%m-%d")
        if now.hour >= 8 and self.s.get("last_summary_day") != day:
            self.s["last_summary_day"] = day
            self.notify("☀️ Resumen diario\n" + self.status_text())


def run():
    state = load_state()
    r = Round(state)

    if state.get("mode") != C.MODE:  # cambio simulado <-> real: empezar de cero
        state["positions"], state["buys"] = {}, []
        state.pop("start_equity_usd", None)
        state["mode"] = C.MODE

    cmds = telegram.read_commands(state)
    r.refresh()
    state.setdefault("start_equity_usd", round(r.equity, 2))
    if (state.get("telegram") or {}).pop("new_owner", False):
        r.notify("👋 Conectado. Te avisaré de cada compra y venta.\n" + HELP)

    r.handle_commands(cmds)
    if not state.get("paused"):
        r.take_profits()
        r.refresh()
        try:
            r.think_and_act()
        except Exception as e:
            print(f"[ronda] error al pensar/actuar: {e}")
        r.refresh()
    r.daily_summary()
    state["history"].append([int(r.now), round(r.equity, 2)])
    state["history"] = state["history"][-2000:]
    state["last_run"] = iso(r.now)
    save_state(state)
    print(f"[ronda] capital {usd(r.equity)} | posiciones {len(state['positions'])} | "
          f"IA mes {usd(state['ai'].get('spent_month', 0))}")


if __name__ == "__main__":
    try:
        run()
    except SystemExit:
        raise
    except Exception as e:
        print(f"[fatal] {e}")
        sys.exit(1)
