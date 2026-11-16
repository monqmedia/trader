"""La IA: un vigilante barato y un decisor potente."""
import json

from . import config as C

WATCHER_SYSTEM = """You watch a small, high-risk Solana meme-coin portfolio every 30 minutes.
You do NOT trade. You decide whether the expensive decision-maker AI must look now.
Escalate when there is something actionable: a free slot plus a candidate with strong, fresh momentum
(rising volume, buyers outnumbering sellers, a pullback after a first pump), or a held position whose
hype is clearly dying (volume collapsing, sellers dominating, sharp drop) or exploding.
Do not escalate for noise. Be strict: each escalation costs money. Write `reason` in Spanish, short."""

DECIDER_SYSTEM = """You are the portfolio manager of a deliberately aggressive Solana meme-coin account.
The owner chose high risk / high reward ("all or nothing") and accepts losing everything, but wants
SMART bets, not random ones. Your goal: maximize the chance of multiplying the account.

Portfolio rules (enforced by code anyway):
- At most {max_pos} positions; each buy uses the standard size times your size_fraction (0.5-1.0).
- You can only buy mints from the CANDIDATES list (they already passed safety checks: no mint/freeze
  authority, no transfer tax, holders not too concentrated, sellable). Never invent mints.
- Code automatically sells 1/3 at x3 and another 1/3 at x6. The rest is the moonbag: you decide.
- There is NO automatic stop-loss. Exiting losers is your job.

How to pick (in order of importance):
1. Narrative: meme coins move on attention. Prefer coins riding a current narrative/trend with real
   community activity. If web search is available, use at most 2 searches to check hype on the best
   1-2 candidates (X/Twitter mentions, news). Skip searching when nothing is worth buying.
2. Real demand: volume rising across m5->h1->h6, many distinct buyers, buys > sells, healthy
   volume/liquidity. Be suspicious of paid promotion ("promocionada") without organic volume.
3. Entry timing: buy after a first pump and a pullback/consolidation, never chase a vertical candle
   (e.g. +80% in the last hour with no pause).
4. Portfolio mix: ideally 1 "consolidada" (steadier, x2-x5 potential) and 1-2 "joven" (x10+ potential).
   Do not hold two coins of the same narrative.
5. Exits: sell a position (fully or partly) when its narrative dies: volume collapsing, sellers
   dominating for hours, big holders leaving. Do not wait for losers to "come back". Rotate capital
   into stronger setups. Holding is fine while the trend is alive.
6. Doing nothing is a valid decision. Only act with a clear edge; fees and slippage cost ~2-5% per trip.

Learn from the journal and closed trades you are given.
Write every `reason`, `market_view` and `notes_for_next_time` in simple Spanish, no jargon, 1-2 sentences.
For buys set sell_fraction to 0. For sells set size_fraction to 0."""

WATCHER_SCHEMA = {
    "type": "object",
    "properties": {"escalate": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["escalate", "reason"],
    "additionalProperties": False,
}

DECIDER_SCHEMA = {
    "type": "object",
    "properties": {
        "market_view": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["buy", "sell"]},
                    "mint": {"type": "string"},
                    "size_fraction": {"type": "number"},
                    "sell_fraction": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["type", "mint", "size_fraction", "sell_fraction", "reason"],
                "additionalProperties": False,
            },
        },
        "notes_for_next_time": {"type": "string"},
    },
    "required": ["market_view", "actions", "notes_for_next_time"],
    "additionalProperties": False,
}


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=C.ANTHROPIC_API_KEY, max_retries=3)


def _cost(model, usage, searches=0):
    pin, pout = C.PRICES.get(model, (4.0, 20.0))
    inp = (usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0) \
        + (getattr(usage, "cache_read_input_tokens", 0) or 0)
    return inp * pin / 1e6 + (usage.output_tokens or 0) * pout / 1e6 + searches * C.WEB_SEARCH_USD


def _final_json(resp):
    if resp.stop_reason == "refusal":
        raise RuntimeError("la IA rechazó la petición")
    texts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    if not texts:
        raise RuntimeError(f"respuesta sin texto (stop_reason={resp.stop_reason})")
    return json.loads(texts[-1])


def watch(snapshot):
    """Devuelve (decisión, coste_usd)."""
    resp = _client().messages.create(
        model=C.WATCHER_MODEL,
        max_tokens=4000,
        system=WATCHER_SYSTEM,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": WATCHER_SCHEMA}},
        messages=[{"role": "user", "content": json.dumps(snapshot, ensure_ascii=False)}],
    )
    return _final_json(resp), _cost(C.WATCHER_MODEL, resp.usage)


def decide(snapshot):
    """Devuelve (decisión, coste_usd)."""
    client = _client()
    system = DECIDER_SYSTEM.format(max_pos=C.MAX_POSITIONS)
    messages = [{"role": "user", "content": json.dumps(snapshot, ensure_ascii=False)}]
    kwargs = dict(
        model=C.DECIDER_MODEL,
        max_tokens=16000,
        system=system,
        output_config={"effort": C.DECIDER_EFFORT,
                       "format": {"type": "json_schema", "schema": DECIDER_SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if C.WEB_SEARCH:
        kwargs["tools"] = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 2}]

    cost = 0.0
    try:
        resp = client.beta.messages.create(messages=messages, **kwargs)
    except Exception as e:  # si la búsqueda web o el respaldo fallan, se intenta sin ellos
        print(f"[ia] reintento simple tras error: {e}")
        for k in ("tools", "betas", "fallbacks"):
            kwargs.pop(k, None)
        resp = client.messages.create(messages=messages, **kwargs)

    for _ in range(3):  # la búsqueda web puede pausar el turno
        searches = sum(1 for b in resp.content if getattr(b, "type", "") == "server_tool_use")
        cost += _cost(C.DECIDER_MODEL, resp.usage, searches)
        if resp.stop_reason != "pause_turn":
            break
        messages = messages + [{"role": "assistant", "content": resp.content}]
        resp = client.beta.messages.create(messages=messages, **kwargs)
    return _final_json(resp), cost
