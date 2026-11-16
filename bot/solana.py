"""Cuenta de Solana: saldos y compraventa a través de Jupiter."""
import base64
import json
import time

from . import config as C
from .net import get_json, post_json

TOKEN_PROGRAMS = [
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
]


class Rpc:
    def __init__(self, url):
        self.url = url

    def call(self, method, params):
        r = post_json(self.url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if "error" in r:
            raise RuntimeError(f"RPC {method}: {r['error']}")
        return r["result"]


class Jupiter:
    def __init__(self, base=C.JUPITER_BASE, key=C.JUPITER_API_KEY):
        self.base = base.rstrip("/")
        self.headers = {"x-api-key": key} if key else None

    def quote(self, in_mint, out_mint, amount, slippage_bps):
        q = get_json(f"{self.base}/swap/v1/quote", {
            "inputMint": in_mint, "outputMint": out_mint, "amount": str(int(amount)),
            "slippageBps": slippage_bps, "restrictIntermediateTokens": "true",
        }, headers=self.headers)
        if not q or "outAmount" not in q:
            raise RuntimeError(f"sin ruta de compraventa: {q}")
        return q

    def swap_tx(self, quote, pubkey):
        r = post_json(f"{self.base}/swap/v1/swap", {
            "quoteResponse": quote,
            "userPublicKey": pubkey,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": {"priorityLevelWithMaxLamports": {
                "maxLamports": C.PRIORITY_FEE_MAX_LAMPORTS, "priorityLevel": "veryHigh"}},
        }, headers=self.headers)
        if "swapTransaction" not in r:
            raise RuntimeError(f"Jupiter no devolvió transacción: {r}")
        return r["swapTransaction"]


def sol_price_usd(jup):
    q = jup.quote(C.SOL_MINT, C.USDC_MINT, 10**9, 50)
    return int(q["outAmount"]) / 1e6


def value_in_sol(jup, mint, amount):
    """Lamports que se obtendrían vendiendo `amount` ahora mismo (0 si no hay ruta)."""
    if amount <= 0:
        return 0
    try:
        return int(jup.quote(mint, C.SOL_MINT, amount, C.SELL_SLIPPAGE_BPS)["outAmount"])
    except Exception:
        return 0


B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(s):
    n = 0
    for ch in s:
        n = n * 58 + B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * (len(s) - len(s.lstrip("1"))) + raw


def load_keypair(secret):
    """Acepta la clave en base58, hexadecimal o lista JSON; de 32 bytes (semilla) o 64."""
    from solders.keypair import Keypair
    s = secret.strip().strip('"').strip()
    if s.lower().startswith("0x"):
        s = s[2:]
    if s.startswith("["):
        raw = bytes(json.loads(s))
    elif len(s) in (64, 128) and all(c in "0123456789abcdefABCDEF" for c in s):
        raw = bytes.fromhex(s)
    elif s and all(c in B58 for c in s):
        raw = b58decode(s)
    else:
        raise ValueError(f"la clave privada no tiene un formato reconocible ({len(s)} caracteres)")
    if len(raw) == 32:
        return Keypair.from_seed(raw)
    if len(raw) == 64:
        return Keypair.from_bytes(raw)
    raise ValueError(f"la clave privada mide {len(raw)} bytes; se esperaban 32 o 64")


class RealWallet:
    def __init__(self, secret, rpc, jup):
        self.kp = load_keypair(secret)
        self.pubkey = str(self.kp.pubkey())
        self.rpc, self.jup = rpc, jup

    def sol_lamports(self):
        return int(self.rpc.call("getBalance", [self.pubkey, {"commitment": "confirmed"}])["value"])

    def token_balances(self):
        out = {}
        for prog in TOKEN_PROGRAMS:
            res = self.rpc.call("getTokenAccountsByOwner", [
                self.pubkey, {"programId": prog}, {"encoding": "jsonParsed", "commitment": "confirmed"}])
            for acc in res["value"]:
                info = acc["account"]["data"]["parsed"]["info"]
                amt = int(info["tokenAmount"]["amount"])
                if amt > 0:
                    out[info["mint"]] = out.get(info["mint"], 0) + amt
        return out

    def _balance_of(self, mint):
        if mint == C.SOL_MINT:
            return self.sol_lamports()
        return self.token_balances().get(mint, 0)

    def swap(self, quote):
        from solders.transaction import VersionedTransaction
        out_mint = quote["outputMint"]
        before = self._balance_of(out_mint)
        raw = VersionedTransaction.from_bytes(base64.b64decode(self.jup.swap_tx(quote, self.pubkey)))
        signed = VersionedTransaction(raw.message, [self.kp])
        sig = self.rpc.call("sendTransaction", [
            base64.b64encode(bytes(signed)).decode(),
            {"encoding": "base64", "skipPreflight": True, "maxRetries": 5}])
        status = self._wait(sig)
        if status != "ok":
            return {"ok": False, "sig": sig, "error": status}
        for _ in range(5):
            got = self._balance_of(out_mint) - before
            if got > 0:
                break
            time.sleep(2)
        return {"ok": True, "sig": sig, "in_amount": int(quote["inAmount"]),
                "out_amount": max(got, 0) or int(quote["outAmount"])}

    def _wait(self, sig, timeout=75):
        end = time.time() + timeout
        while time.time() < end:
            st = self.rpc.call("getSignatureStatuses", [[sig], {"searchTransactionHistory": True}])["value"][0]
            if st:
                if st.get("err"):
                    return f"falló en la red: {st['err']}"
                if st.get("confirmationStatus") in ("confirmed", "finalized"):
                    return "ok"
            time.sleep(3)
        return "no se confirmó a tiempo"


class PaperWallet:
    """Cuenta simulada: usa precios reales, pero no mueve dinero."""

    def __init__(self, state, jup, sol_usd):
        self.jup = jup
        p = state.setdefault("paper", {})
        if "sol_lamports" not in p:
            p["sol_lamports"] = int(C.PAPER_START_USD / sol_usd * 1e9)
            p["tokens"] = {}
        self.p = p
        self.pubkey = "SIMULADA"

    def sol_lamports(self):
        return self.p["sol_lamports"]

    def token_balances(self):
        return {m: a for m, a in self.p["tokens"].items() if a > 0}

    def swap(self, quote):
        i, o = quote["inputMint"], quote["outputMint"]
        ain, aout = int(quote["inAmount"]), int(quote["outAmount"])
        fee = 20_000  # comisión aproximada en lamports
        if i == C.SOL_MINT:
            if self.p["sol_lamports"] < ain + fee:
                return {"ok": False, "error": "saldo insuficiente"}
            self.p["sol_lamports"] -= ain + fee
            self.p["tokens"][o] = self.p["tokens"].get(o, 0) + aout
        else:
            if self.p["tokens"].get(i, 0) < ain:
                return {"ok": False, "error": "saldo insuficiente"}
            self.p["tokens"][i] -= ain
            self.p["sol_lamports"] += aout - fee
        return {"ok": True, "sig": "simulada", "in_amount": ain, "out_amount": aout}
