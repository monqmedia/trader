# Inversor IA de meme coins (Solana)

Cada 30 minutos GitHub lanza una ronda:

1. **Busca** meme coins de Solana en tendencia (GeckoTerminal, DexScreener).
2. **Filtra** las que tienen poca liquidez, son demasiado nuevas o tienen poco volumen.
3. **Revisa la seguridad** con RugCheck. Descarta las que tienen creador con poderes (fabricar o congelar monedas), comisiones ocultas, pocas cuentas con demasiadas monedas o historial de estafa. Antes de comprar, prueba que se pueden vender.
4. **Vigilante IA** (Claude Haiku, barato): decide si hay algo que merezca la pena mirar.
5. **Decisor IA** (Claude Opus, potente): analiza moda, demanda y momento. Puede buscar en internet si hay hype real. Después decide comprar, vender o esperar.
6. **Ventas automáticas por beneficio:** vende 1/3 a x3 y otro 1/3 a x6. El resto lo gestiona la IA.
7. **Te avisa por ntfy** (o Telegram) de cada movimiento.

Estrategia: máximo 3 apuestas, sin stop-loss automático. Reinvierte las ganancias. La memoria (posiciones, diario de decisiones, operaciones cerradas) se guarda en `state/state.json`.

## Puesta en marcha

### 1. Claves en GitHub
Repositorio → **Settings → Secrets and variables → Actions → New repository secret**:

| Nombre | Qué es |
|---|---|
| `ANTHROPIC_API_KEY` | Clave de console.anthropic.com |
| `NTFY_TOPIC` | Nombre secreto del canal de avisos en ntfy |
| `TELEGRAM_BOT_TOKEN` | Opcional: código del bot que da @BotFather |
| `SOLANA_PRIVATE_KEY` | Solo para modo real. Clave privada de la **cuenta nueva** dedicada |
| `SOLANA_RPC_URL` | Opcional, recomendado en real: un RPC gratuito de helius.dev, más fiable |

### 2. Avisos (ntfy)
Instala la app **ntfy**, pulsa **+** y suscríbete al tema guardado en `NTFY_TOPIC`. Quien conozca ese nombre puede leer los avisos y dar órdenes, así que no lo compartas.

Para dar órdenes, abre `https://ntfy.sh/<tu tema>` en el navegador y escribe: `/estado`, `/pausa`, `/seguir` o `/vender_todo`. La respuesta llega en la próxima ronda (máximo 30 minutos).

### 3. Encenderlo
Pestaña **Actions** → **inversor** → **Run workflow**. A partir de ahí se ejecuta solo cada 30 minutos.

### 4. Modo simulado → real
Empieza en **simulado**: usa precios reales con $120 ficticios y no toca dinero.

Para pasar a real: **Settings → Secrets and variables → Actions → pestaña Variables → New variable**, nombre `MODE`, valor `real`. Además tiene que estar puesto el secreto `SOLANA_PRIVATE_KEY`.

## Costes
- IA: tope de $25/mes (variable `AI_MONTHLY_BUDGET_USD`). Al llegar al tope, la IA se detiene, pero las ventas automáticas por beneficio siguen funcionando.
- GitHub: unos 1.500-1.800 minutos/mes de los 2.000 gratuitos de una cuenta gratuita.
- Comisiones de la red Solana: céntimos por operación.

## Pruebas
```
pip install -r requirements.txt pytest
python -m pytest -q
```
