"""Ajustes del sistema. Todo se puede cambiar con variables de entorno."""
import os


def _env(name, default):
    v = os.getenv(name)
    return default if v is None or v.strip() == "" else v.strip()


def _f(name, default):
    return float(_env(name, default))


def _i(name, default):
    return int(float(_env(name, default)))


# "paper" = simulado (no toca dinero real). "real" = opera con la cuenta.
MODE = _env("MODE", "paper").lower()

# --- Claves (vienen de los Secrets de GitHub) ---
SOLANA_PRIVATE_KEY = _env("SOLANA_PRIVATE_KEY", "")
SOLANA_ACCOUNT_INDEX = _i("SOLANA_ACCOUNT_INDEX", 0)  # con 12 palabras: 0 = Account 1
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID", "")
NTFY_TOPIC = _env("NTFY_TOPIC", "")
NTFY_SERVER = _env("NTFY_SERVER", "https://ntfy.sh")
SOLANA_RPC_URL = _env("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
JUPITER_API_KEY = _env("JUPITER_API_KEY", "")
JUPITER_BASE = _env(
    "JUPITER_BASE", "https://api.jup.ag" if JUPITER_API_KEY else "https://lite-api.jup.ag"
)

# --- Estrategia "todo o nada" ---
MAX_POSITIONS = _i("MAX_POSITIONS", 5)
MIN_POSITION_USD = _f("MIN_POSITION_USD", 10)
MAX_BUYS_PER_DAY = _i("MAX_BUYS_PER_DAY", 6)
REBUY_COOLDOWN_H = _f("REBUY_COOLDOWN_H", 24)
SOL_RESERVE = _f("SOL_RESERVE", 0.03)  # SOL que siempre se guarda para comisiones
MIN_SOL_TO_OPERATE = _f("MIN_SOL_TO_OPERATE", 0.005)  # sin esto no se puede pagar ni una comisión
PAPER_START_USD = _f("PAPER_START_USD", 120)

# Ventas por tramos: (multiplicador, fracción de la cantidad original a vender)
TAKE_PROFITS = [(3.0, 1 / 3), (6.0, 1 / 3)]

# Filtros de entrada
YOUNG_MCAP = (1e6, 25e6)
ESTABLISHED_MCAP = (25e6, 500e6)
YOUNG_MIN_LIQ = _f("YOUNG_MIN_LIQ", 60e3)
ESTABLISHED_MIN_LIQ = _f("ESTABLISHED_MIN_LIQ", 400e3)
YOUNG_MIN_VOL24 = _f("YOUNG_MIN_VOL24", 250e3)
ESTABLISHED_MIN_VOL24 = _f("ESTABLISHED_MIN_VOL24", 1e6)
MIN_AGE_H = _f("MIN_AGE_H", 6)
MAX_TOP10_PCT = _f("MAX_TOP10_PCT", 30)
MAX_CANDIDATES_TO_VET = _i("MAX_CANDIDATES_TO_VET", 12)
MAX_ROUNDTRIP_LOSS = _f("MAX_ROUNDTRIP_LOSS", 0.15)  # prueba de "¿se puede vender?"

# Ejecución
BUY_SLIPPAGE_BPS = _i("BUY_SLIPPAGE_BPS", 400)
SELL_SLIPPAGE_BPS = _i("SELL_SLIPPAGE_BPS", 800)
PANIC_SLIPPAGE_BPS = _i("PANIC_SLIPPAGE_BPS", 1500)  # segundo intento si una venta falla
PRIORITY_FEE_MAX_LAMPORTS = _i("PRIORITY_FEE_MAX_LAMPORTS", 1_000_000)

# --- Vigilancia rápida (bucle minuto a minuto dentro de cada ejecución) ---
GUARD_MINUTES = _f("GUARD_MINUTES", 0)  # 0 = sin bucle (una ronda y fin)
GUARD_INTERVAL_S = _f("GUARD_INTERVAL_S", 60)  # cada cuánto mira precios
FULL_ROUND_MIN = _f("FULL_ROUND_MIN", 10)  # cada cuánto busca monedas nuevas y consulta al vigilante
CRASH_DROP = _f("CRASH_DROP", 0.35)  # caída que provoca venta inmediata...
CRASH_WINDOW_MIN = _f("CRASH_WINDOW_MIN", 10)  # ...si ocurre en estos minutos
ALERT_MOVE = _f("ALERT_MOVE", 0.15)  # subida o bajada que despierta al decisor...
ALERT_WINDOW_MIN = _f("ALERT_WINDOW_MIN", 15)  # ...en estos minutos
TRAIL_DROP = _f("TRAIL_DROP", 0.45)  # tras asegurar beneficios: vende el resto si cae esto desde su máximo
DECIDER_MIN_GAP_MIN = _f("DECIDER_MIN_GAP_MIN", 15)  # mínimo entre consultas al decisor por alertas
UPDATE_EVERY_MIN = _f("UPDATE_EVERY_MIN", 10)  # resumen silencioso de la cartera (0 = desactivado)
QUIET_DAILY_CAP = _i("QUIET_DAILY_CAP", 160)  # ntfy gratis admite ~250 mensajes/día: se reserva sitio para los avisos importantes

# --- IA ---
WATCHER_MODEL = _env("WATCHER_MODEL", "claude-haiku-5-5")
DECIDER_MODEL = _env("DECIDER_MODEL", "claude-opus-5-5")
DECIDER_EFFORT = _env("DECIDER_EFFORT", "high")
AI_MONTHLY_BUDGET_USD = _f("AI_MONTHLY_BUDGET_USD", 25)
DECIDER_MAX_INTERVAL_H = _f("DECIDER_MAX_INTERVAL_H", 6)  # revisión obligatoria cada X horas
WEB_SEARCH = _env("WEB_SEARCH", "on") == "on"
# $ por millón de tokens (entrada, salida)
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
}
WEB_SEARCH_USD = 0.01

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
# Monedas que no son meme coins y nunca se compran
EXCLUDED_MINTS = {
    SOL_MINT,
    USDC_MINT,
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",  # mSOL
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",  # jitoSOL
    "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1",  # bSOL
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  # JUP
    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",  # RAY
}

STATE_PATH = _env("STATE_PATH", "state/state.json")
