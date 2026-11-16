"""Pruebas sin red: reglas de cartera, filtros de seguridad y una ronda simulada completa."""
import json
import time

import pytest

from bot import config as C
from bot import market, portfolio as P

MEME = "Meme111111111111111111111111111111111111111"
SCAM = "Scam111111111111111111111111111111111111111"
BIG = "Bigg111111111111111111111111111111111111111"


def pair(mint, sym, mcap, liq, vol24, age_h, chg_h1=10):
    return {
        "chainId": "solana", "dexId": "raydium",
        "baseToken": {"address": mint, "symbol": sym, "name": sym},
        "priceUsd": "0.001", "marketCap": mcap, "liquidity": {"usd": liq},
        "volume": {"m5": vol24 / 200, "h1": vol24 / 20, "h6": vol24 / 4, "h24": vol24},
        "priceChange": {"m5": 1, "h1": chg_h1, "h6": 20, "h24": 50},
        "txns": {"h1": {"buys": 300, "sells": 200}, "h24": {"buys": 5000, "sells": 4000}},
        "pairCreatedAt": (time.time() - age_h * 3600) * 1000,
        "info": {"socials": [{"type": "twitter"}]},
    }


SAFE_REPORT = {"mintAuthority": None, "freezeAuthority": None, "rugged": False,
               "topHolders": [{"address": "a", "pct": 5}, {"address": "b", "pct": 4}],
               "risks": [{"name": "Low amount of LP Providers", "level": "warn"}],
               "totalHolders": 9000, "score_normalised": 3}


# ---------- reglas ----------

def test_tier():
    assert market.tier(market.parse_pair(pair(MEME, "M", 5e6, 150e3, 2e6, 48))) == "joven"
    assert market.tier(market.parse_pair(pair(MEME, "M", 5e6, 150e3, 2e6, 1))) is None  # muy nueva
    assert market.tier(market.parse_pair(pair(BIG, "B", 200e6, 3e6, 20e6, 900))) == "consolidada"
    assert market.tier(market.parse_pair(pair(MEME, "M", 5e6, 10e3, 2e6, 48))) is None  # poca liquidez


def test_judge_report():
    ok, probs, warns, data = market.judge_report(SAFE_REPORT, "joven")
    assert ok and data["top10_pct"] == 9
    bad = {**SAFE_REPORT, "mintAuthority": "X"}
    assert not market.judge_report(bad, "joven")[0]
    whales = {**SAFE_REPORT, "topHolders": [{"address": str(i), "pct": 8} for i in range(10)]}
    assert not market.judge_report(whales, "joven")[0]
    fraction = {**SAFE_REPORT, "topHolders": [{"address": str(i), "pct": 0.08} for i in range(10)]}
    assert not market.judge_report(fraction, "joven")[0]  # 80% en formato fracción
    lp = {**SAFE_REPORT, "risks": [{"name": "Large Amount of LP Unlocked", "level": "danger"}]}
    assert not market.judge_report(lp, "joven")[0]
    assert market.judge_report(lp, "consolidada")[0]
    pool = {**SAFE_REPORT, "knownAccounts": {"pool": {"type": "AMM"}},
            "topHolders": [{"address": "pool", "pct": 60}, {"address": "x", "pct": 3}]}
    assert market.judge_report(pool, "joven")[0]  # la reserva del mercado no cuenta


def test_take_profit_and_multiple():
    pos = {"cost_usd": 40, "amount_initial": 1000, "tp_stage": 0}
    assert P.price_multiple(pos, 1000, 120) == pytest.approx(3.0)
    assert P.take_profit_due(pos, 2.9) is None
    assert P.take_profit_due(pos, 3.1) == (0, 1 / 3)
    pos["tp_stage"] = 1
    assert P.take_profit_due(pos, 4) is None
    assert P.take_profit_due(pos, 6.5) == (1, 1 / 3)
    pos["tp_stage"] = 2
    assert P.take_profit_due(pos, 50) is None  # el resto lo decide la IA


def test_validate(monkeypatch):
    monkeypatch.setattr(C, "MAX_POSITIONS", 3)
    positions = {"H1": {}, "H2": {}}
    cands = [{"mint": "C1"}, {"mint": "C2"}]
    acts = [
        {"type": "buy", "mint": "C1", "size_fraction": 3, "sell_fraction": 0, "reason": ""},
        {"type": "buy", "mint": "C2", "size_fraction": 1, "sell_fraction": 0, "reason": ""},
        {"type": "buy", "mint": "INVENTADA", "size_fraction": 1, "sell_fraction": 0, "reason": ""},
        {"type": "sell", "mint": "NOTENGO", "size_fraction": 0, "sell_fraction": 1, "reason": ""},
    ]
    sells, buys, rej = P.validate(acts, positions, cands, buys_left=4)
    assert [b["mint"] for b in buys] == ["C1"]  # solo 1 hueco libre
    assert buys[0]["size_fraction"] == 1.0
    assert not sells and len(rej) == 3
    acts.append({"type": "sell", "mint": "H1", "size_fraction": 0, "sell_fraction": 0.97, "reason": ""})
    sells, buys, _ = P.validate(acts, positions, cands, buys_left=4)
    assert sells[0]["sell_fraction"] == 1.0 and len(buys) == 2  # la venta libera hueco
    _, buys, _ = P.validate(acts, positions, cands, buys_left=0)
    assert not buys


def test_standard_buy():
    assert P.standard_buy_usd(120, 120, 150) == pytest.approx(120 / C.MAX_POSITIONS)
    assert P.standard_buy_usd(120, 20, 150) == pytest.approx(20 - C.SOL_RESERVE * 150)


# ---------- ronda simulada completa ----------

class FakeNet:
    def __init__(self):
        self.sent = []
        self.updates = [{"update_id": 1, "message": {"chat": {"id": 77}, "text": "/start"}}]
        self.price_mult = {MEME: 1.0, BIG: 1.0}

    def get(self, url, params=None, headers=None, tries=3):
        if "geckoterminal" in url:
            def pool(m):
                return {"attributes": {"transactions": {"h1": {"buyers": 250, "sellers": 120}}},
                        "relationships": {"base_token": {"data": {"id": f"solana_{m}"}}}}
            return {"data": [pool(MEME), pool(SCAM), pool(BIG), pool(C.SOL_MINT)]}
        if "token-boosts" in url:
            return []
        if "dexscreener.com/tokens" in url:
            all_pairs = [pair(MEME, "MEME", 5e6, 150e3, 2e6, 48), pair(SCAM, "SCAM", 3e6, 90e3, 1e6, 30),
                         pair(BIG, "BIG", 200e6, 3e6, 20e6, 900), pair(C.SOL_MINT, "SOL", 8e10, 1e8, 1e9, 9999)]
            return [p for p in all_pairs if p["baseToken"]["address"] in url]
        if "rugcheck" in url:
            return {**SAFE_REPORT, "freezeAuthority": "X"} if SCAM in url else SAFE_REPORT
        if "/swap/v1/quote" in url:
            i, o, a = params["inputMint"], params["outputMint"], int(params["amount"])
            if i == C.SOL_MINT and o == C.USDC_MINT:
                out = a * 150 // 1000  # 1 SOL = 150 USDC
            elif i == C.USDC_MINT:
                out = int(a / 1e6 / 150 * 1e9)
            elif i == C.SOL_MINT:
                out = a * 1000  # 1 lamport = 1000 unidades
            else:
                out = int(a / 1000 * 0.98 * self.price_mult[i])
            return {"inputMint": i, "outputMint": o, "inAmount": str(a), "outAmount": str(out),
                    "priceImpactPct": "0.001"}
        if "getUpdates" in url:
            u, self.updates = self.updates, []
            return {"ok": True, "result": u}
        raise AssertionError(url)

    def post(self, url, body, headers=None, tries=3):
        if "sendMessage" in url:
            self.sent.append(body["text"])
            return {"ok": True}
        raise AssertionError(url)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from bot import __main__ as M, brain, solana, telegram
    net = FakeNet()
    for mod in (market, solana, telegram):
        monkeypatch.setattr(mod, "get_json", net.get, raising=False)
        monkeypatch.setattr(mod, "post_json", net.post, raising=False)
    monkeypatch.setattr(market.time, "sleep", lambda s: None)
    monkeypatch.setattr(C, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(C, "MODE", "paper")
    monkeypatch.setattr(C, "ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(C, "TELEGRAM_BOT_TOKEN", "test")
    monkeypatch.setattr(C, "TELEGRAM_CHAT_ID", "")
    monkeypatch.setattr(C, "NTFY_TOPIC", "")
    calls = {"watch": 0, "decide": [], "snaps": []}

    def watch(snap):
        calls["watch"] += 1
        return {"escalate": True, "reason": "hay oportunidad"}, 0.001

    def decide(snap):
        calls["snaps"].append(snap)
        return calls["decide"].pop(0), 0.2

    monkeypatch.setattr(brain, "watch", watch)
    monkeypatch.setattr(brain, "decide", decide)
    return M, net, calls


def test_full_paper_rounds(env):
    M, net, calls = env
    calls["decide"].append({"market_view": "fuerte", "notes_for_next_time": "", "actions": [
        {"type": "buy", "mint": MEME, "size_fraction": 1, "sell_fraction": 0, "reason": "moda fuerte"},
        {"type": "buy", "mint": SCAM, "size_fraction": 1, "sell_fraction": 0, "reason": "trampa"},
    ]})
    M.run()
    st = json.load(open(C.STATE_PATH))
    snap = calls["snaps"][0]
    assert {c["mint"] for c in snap["candidates"]} == {MEME, BIG}  # SCAM y SOL filtradas
    assert list(st["positions"]) == [MEME]  # SCAM rechazada por no estar verificada
    pos = st["positions"][MEME]
    assert pos["cost_usd"] == pytest.approx(120 / C.MAX_POSITIONS, rel=0.01)
    assert st["telegram"]["chat_id"] == "77"
    assert any("Compra MEME" in m for m in net.sent)
    assert st["start_equity_usd"] == pytest.approx(120, rel=0.01)

    # El precio se multiplica x3.5 -> venta automática de 1/3; la IA espera
    net.price_mult[MEME] = 3.5
    calls["decide"].append({"market_view": "esperar", "notes_for_next_time": "", "actions": []})
    st["ai"]["last_decider"] = 0
    json.dump(st, open(C.STATE_PATH, "w"))
    M.run()
    st = json.load(open(C.STATE_PATH))
    assert st["positions"][MEME]["tp_stage"] == 1
    assert st["positions"][MEME]["realized_usd"] > pos["cost_usd"]
    assert any("Venta parcial" in m for m in net.sent)

    # Orden por Telegram: vender todo y pausar
    net.updates = [{"update_id": 2, "message": {"chat": {"id": 77}, "text": "/vender_todo"}},
                   {"update_id": 3, "message": {"chat": {"id": 99}, "text": "/seguir"}}]  # extraño: ignorado
    M.run()
    st = json.load(open(C.STATE_PATH))
    assert st["paused"] is True and not st["positions"]
    closed = st["closed"][-1]
    assert closed["symbol"] == "MEME" and closed["result_multiple"] > 3
    assert calls["decide"] == []  # en pausa no se consulta a la IA


def test_ntfy(monkeypatch):
    from bot import ntfy

    class Resp:
        def __init__(self, text=""):
            self.text = text

        def raise_for_status(self):
            pass

    lines = [
        {"id": "a1", "event": "open"},
        {"id": "a2", "event": "message", "title": "Inversor", "message": "/estado"},  # propio: ignorar
        {"id": "a3", "event": "message", "message": "/Pausa"},
        {"id": "a4", "event": "message", "message": "hola"},
    ]
    posted = []
    monkeypatch.setattr(C, "NTFY_TOPIC", "tema-secreto")
    monkeypatch.setattr(ntfy.requests, "get",
                        lambda url, params, timeout: Resp("\n".join(json.dumps(x) for x in lines)))
    monkeypatch.setattr(ntfy.requests, "post",
                        lambda url, data, headers, timeout: posted.append((url, data, headers)) or Resp())
    state = {}
    assert ntfy.read_commands(state) == ["/pausa"]
    assert state["ntfy"]["since"] == "a4" and state["ntfy"]["new_owner"]
    ntfy.send(state, "Compra MEME")
    assert posted[0][0].endswith("/tema-secreto") and posted[0][2]["Title"] == "Inversor"


def test_real_mode_needs_sol_and_converts_usdc(env, monkeypatch):
    M, net, calls = env

    class FakeReal:
        pubkey = "MiDireccion"

        def __init__(self, sol):
            self.sol, self.usdc, self.swaps = sol, 126_000_000, []

        def sol_lamports(self):
            return self.sol

        def token_balances(self):
            return {C.USDC_MINT: self.usdc} if self.usdc else {}

        def swap(self, q):
            self.swaps.append(q)
            self.usdc = 0
            self.sol += 840_000_000
            return {"ok": True, "in_amount": int(q["inAmount"]), "out_amount": 840_000_000}

    monkeypatch.setattr(C, "MODE", "real")
    monkeypatch.setattr(C, "SOLANA_PRIVATE_KEY", "x")
    wallet = FakeReal(sol=0)
    monkeypatch.setattr(M, "RealWallet", lambda *a: wallet)
    M.run()  # sin SOL: avisa y no opera
    st = json.load(open(C.STATE_PATH))
    assert any("necesita un poco de SOL" in m for m in net.sent)
    assert not wallet.swaps and "start_equity_usd" not in st and calls["snaps"] == []

    wallet.sol = 20_000_000  # llegan 0.02 SOL
    calls["decide"].append({"market_view": "", "notes_for_next_time": "", "actions": []})
    M.run()
    st = json.load(open(C.STATE_PATH))
    assert wallet.swaps[0]["inputMint"] == C.USDC_MINT
    assert any("Convertidos 126.00 USDC" in m for m in net.sent)
    assert st["start_equity_usd"] == pytest.approx(0.86 * 150, rel=0.01)


def test_load_keypair_formats():
    from solders.keypair import Keypair
    from bot.solana import load_keypair
    kp = Keypair()
    full = bytes(kp)
    seed = full[:32]
    addr = str(kp.pubkey())
    for s in (str(kp), full.hex(), "0x" + seed.hex(), seed.hex(), json.dumps(list(full)), f' "{kp}" \n'):
        assert str(load_keypair(s).pubkey()) == addr
    with pytest.raises(ValueError):
        load_keypair("hola mundo")


def test_slip10_vectors():
    from bot.solana import slip10_ed25519, keypair_from_mnemonic, load_keypair
    seed = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    assert slip10_ed25519(seed, []).hex() == "2b4be7f19ee27bbf30c667b642d5f4aa69fd169872f8fc3059c08ebae2eb19e7"
    assert slip10_ed25519(seed, [0]).hex() == "68e0fe46dfb67e368c75379acec591dad19df3cde26e63b93a8e704f1dade7a3"
    words = "abandon " * 11 + "about"
    a0 = str(keypair_from_mnemonic(words, 0).pubkey())
    assert a0 == "HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk"  # referencia Phantom/MetaMask
    assert str(load_keypair("  " + words.upper() + "\n").pubkey()) == a0
    assert str(keypair_from_mnemonic(words, 1).pubkey()) != a0


def test_price_signal_and_trailing():
    now = 10_000
    calm = [(now - 600 + i * 60, 1.0) for i in range(10)]
    assert P.price_signal(calm, now, 1.02) is None
    assert P.price_signal(calm, now, 0.6) == "crash"  # -40% en 10 min
    assert P.price_signal(calm, now, 0.0) == "crash"  # sin ruta de venta
    assert P.price_signal([], now, 0.0) is None
    assert P.price_signal(calm, now, 0.82) == "alert_down"
    assert P.price_signal(calm, now, 1.2) == "alert_up"
    old = [(now - 3600, 2.0)]  # caída lenta (fuera de la ventana): no es pánico
    assert P.price_signal(old, now, 1.0) is None
    pos = {"tp_stage": 1, "peak_multiple": 5.0}
    assert not P.trailing_exit_due(pos, 3.0)
    assert P.trailing_exit_due(pos, 2.7)
    assert not P.trailing_exit_due({"tp_stage": 0, "peak_multiple": 5.0}, 1.0)


def test_guard_loop_sells_on_crash(env, monkeypatch):
    M, net, calls = env
    clock = [1_000_000.0]
    monkeypatch.setattr(M.time, "time", lambda: clock[0])
    monkeypatch.setattr(M.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(C, "GUARD_MINUTES", 8)
    calls["decide"].append({"market_view": "", "notes_for_next_time": "", "actions": [
        {"type": "buy", "mint": MEME, "size_fraction": 1, "sell_fraction": 0, "reason": "moda"}]})
    original_get = net.get
    step = {"n": 0}

    def get(url, params=None, headers=None, tries=3):
        if "/swap/v1/quote" in url and params["inputMint"] == MEME:
            step["n"] += 1
            if step["n"] > 6:  # tras unos minutos tranquilos, se desploma un 60%
                net.price_mult[MEME] = 0.4
        return original_get(url, params, headers, tries)

    from bot import solana, telegram
    for mod in (market, solana, telegram):
        monkeypatch.setattr(mod, "get_json", get)
    M.run()
    st = json.load(open(C.STATE_PATH))
    assert not st["positions"]
    assert "caída brusca" in st["closed"][-1]["exit_reason"]
    assert any("Venta de emergencia" in m for m in net.sent)


def test_portfolio_update_is_quiet_and_periodic(env, monkeypatch):
    M, net, calls = env
    from bot import ntfy
    sent = []
    monkeypatch.setattr(ntfy, "send", lambda state, text, quiet=False: sent.append((text, quiet)))
    clock = [2_000_000.0]
    monkeypatch.setattr(M.time, "time", lambda: clock[0])
    monkeypatch.setattr(M.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(C, "GUARD_MINUTES", 40)
    monkeypatch.setattr(C, "FULL_ROUND_MIN", 1000)
    monkeypatch.setattr(C, "UPDATE_EVERY_MIN", 15)
    calls["decide"].append({"market_view": "", "notes_for_next_time": "", "actions": []})
    M.run()
    updates = [t for t, q in sent if q and "📈 Cartera" in t]
    assert len(updates) == 3  # minutos ~1, ~16 y ~31 de 40
    # con tope diario de 2, no se envían más aunque toque
    st = json.load(open(C.STATE_PATH))
    st["quiet"] = {"day": st["quiet"]["day"], "count": 0}
    st["last_update"] = 0
    json.dump(st, open(C.STATE_PATH, "w"))
    monkeypatch.setattr(C, "QUIET_DAILY_CAP", 2)
    monkeypatch.setattr(C, "UPDATE_EVERY_MIN", 1)
    sent.clear()
    M.run()
    assert len([t for t, q in sent if q]) == 2
    assert not any(q for t, q in sent if "📈 Cartera" not in t)  # el resto de avisos sí suenan


def test_adopts_untracked_tokens(env, monkeypatch):
    """Si la memoria se pierde tras una compra, la moneda se recupera y se sigue vigilando."""
    M, net, calls = env
    calls["decide"].append({"market_view": "", "notes_for_next_time": "", "actions": []})
    calls["decide"].append({"market_view": "", "notes_for_next_time": "", "actions": []})
    M.run()
    st = json.load(open(C.STATE_PATH))
    st["paper"]["tokens"][MEME] = 200_000_000_000  # (~$29) una moneda en la cuenta que no figura en la memoria
    st["paper"]["tokens"][SCAM] = 1  # polvo: se ignora
    st["ai"]["last_decider"] = 0
    json.dump(st, open(C.STATE_PATH, "w"))
    net.price_mult[SCAM] = 1.0
    M.run()
    st = json.load(open(C.STATE_PATH))
    assert MEME in st["positions"] and SCAM not in st["positions"]
    assert st["positions"][MEME]["entry_reason"].startswith("recuperada")
    assert SCAM in st["ignored_tokens"]
    assert any("Recuperada MEME" in m for m in net.sent)
