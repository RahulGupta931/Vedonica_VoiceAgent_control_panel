# Vedonica — Voice Customer Support Bot

Implements exactly the pipeline in the architecture diagram:

```
Customer Call → Twilio/SIP → STT (Deepgram) → Conversation Manager (Pipecat)
→ LLM (GPT) ⇄ Business Logic/Tools ⇄ [Postgres | External CRM/Calendar APIs]
→ TTS (Cartesia) → customer
```

| Diagram box | File | Real service |
|---|---|---|
| Telephony \| Twilio/SIP | `server.py` (`/voice`, `/ws`) | Twilio Media Streams |
| STT Engine | `app/bot.py` | Deepgram `nova-3`, `language="multi"` |
| Conversation Manager | `app/bot.py` (Pipecat `Pipeline`) | Pipecat |
| LLM Layer | `app/bot.py` | OpenAI `gpt-4o-mini` (swap for Gemini/Llama easily) |
| Business Logic / Tools | `app/tools.py` | Pipecat function calling |
| Database (PostgreSQL) | `app/db.py`, `schema.sql` | asyncpg |
| External APIs (CRM/Calendar) | `app/tools.py` | `httpx` |
| TTS Engine | `app/bot.py` | Cartesia Sonic-2 |

## Why this stack is fast + Hinglish-native + natural

**Hinglish understanding**
- Deepgram `nova-3` with `language="multi"` does live code-switch
  recognition — one customer sentence can mix Hindi and English words and
  it transcribes both correctly, instead of forcing you to pick one
  language up front.
- The system prompt (`app/prompts.py`) explicitly tells the LLM to *mirror*
  the customer's Hinglish ratio rather than translating everything to one
  language — this is what makes it feel like a real support agent, not a
  translation bot.
- Cartesia's multilingual model (`language="hi"` param, and a
  Hindi/Hinglish-capable voice) reads the mixed-script Roman text with
  correct Hindi pronunciation instead of an anglicized accent.

**Fast response (sub-second feel)**
- Every stage streams: Deepgram sends interim transcripts, the LLM streams
  tokens, Cartesia starts producing audio from partial sentences — nothing
  waits for a full turn to complete before the next stage starts working.
- `endpointing=250` (ms) on STT and a `0.5s` VAD `stop_secs` keep
  turn-detection snappy without cutting people off mid-sentence.
- The greeting is **not** LLM-generated — it's spoken instantly via
  `TTSSpeakFrame` the moment the call connects (see `on_client_connected`
  in `app/bot.py`), so the customer never hears dead air waiting on a
  model round-trip.
- `allow_interruptions=True` gives real barge-in: the customer can cut the
  bot off mid-sentence, just like talking to a person.
- Tool calls have a 4s HTTP timeout and fail soft (`{"error": ...}`)
  instead of hanging the call.
- `gpt-4o-mini` is used for its low time-to-first-token; swap
  `LLM_MODEL` for a bigger model only if you need deeper reasoning — the
  quality/latency trade-off matters a lot more on voice than on text.

**Natural output**
- The prompt bans markdown/lists/emojis and enforces short, spoken-style
  sentences — text meant for a screen reads badly out loud.
- Numbers/dates are asked to be spelled out the way a person would say
  them.
- The LLM is instructed to never invent order/account facts — every claim
  is grounded in a tool call, which also avoids the "confidently wrong"
  failure mode that breaks trust fastest on a support line.

## Multi-provider setup + automatic failover (performance upgrade)

`app/config.py` is a factory that builds STT/LLM/TTS from **any** of these,
picked purely via `.env` — no code changes:

| Stage | Providers wired in |
|---|---|
| STT | Deepgram, **Sarvam**, Azure, Google, Speechmatics, Groq |
| LLM | OpenAI, **Groq**, Google, Azure, OpenRouter, Sarvam |
| TTS | Cartesia, **Sarvam**, Smallest, ElevenLabs, Azure, Google, Inworld, Camb, Groq |

**For Hinglish specifically, benchmark Sarvam first.** Unlike the generic
providers, Sarvam's `saaras:v3` STT and `bulbul:v2` TTS are built
ground-up for Indian languages and code-mixed Hindi/English — in practice
this tends to beat generic multilingual models on both accuracy and
naturalness for exactly this use case. Smallest's Lightning TTS
(`SmallestTTSService`) is worth racing against Cartesia too — it
advertises ~64ms time-to-first-audio-byte.

**Automatic failover.** Set a `*_FAILOVER_PROVIDER` in `.env` and that
stage becomes a `ServiceSwitcher`/`LLMSwitcher` with
`ServiceSwitcherStrategyFailover`: if the primary provider throws a
non-fatal error mid-call, Pipecat automatically switches to the backup
for the rest of the call — the customer never hears dead air because one
vendor had a bad moment. Example:

```
STT_PROVIDER=deepgram
STT_FAILOVER_PROVIDER=sarvam
LLM_PROVIDER=openai
LLM_FAILOVER_PROVIDER=groq       # groq is also just faster — consider as primary
TTS_PROVIDER=cartesia
TTS_FAILOVER_PROVIDER=sarvam
```

**How to actually benchmark instead of guessing:** `enable_metrics=True`
is already set on the `PipelineTask` in `app/bot.py` — it logs per-turn
TTFB (time-to-first-byte) for STT/LLM/TTS. Run the same 10-15 real
Hinglish customer sentences through each provider combo and compare those
numbers plus a human listen-through; latency numbers alone don't capture
whether the Hindi actually sounds natural.

**MCP tools.** Set `MCP_SERVER_URL` to auto-register every tool an MCP
server exposes (internal wikis, ticketing systems, etc.) alongside the
tools in `app/tools.py` — no per-tool code needed.

**WebRTC for local iteration.** Dialing a real phone number for every
latency tweak is slow. For fast local iteration, Pipecat also supports a
browser-based WebRTC transport (`webrtc` extra) via
`pipecat.runner.utils.create_transport` — see the
[Pipecat WebRTC quickstart](https://docs.pipecat.ai/getting-started/quickstart)
to spin up a local browser client against the same `app/bot.py` pipeline
logic without touching Twilio at all.

## Dashboard + browser test console

Two UI pieces sit on top of the pipeline described above:

**Dashboard** (`dashboard/`) — a static admin UI served by `server.py` at
`/dashboard`. It's read-only: calls + transcripts, support tickets, orders,
the knowledge base content, and the active STT/LLM/TTS provider config (no
API keys shown). It reads from Postgres via new endpoints in `app/api.py`
(`/api/overview`, `/api/calls`, `/api/tickets`, `/api/orders`,
`/api/knowledge`, `/api/config`) and fails soft to an empty state if
`DATABASE_URL` isn't set yet, so you can look at the UI before Postgres is
wired up. Run the server as usual (`uvicorn server:app --reload --port
7860`) and open `http://localhost:7860/dashboard`.

Calls are now logged automatically: `app/db.py` has a `calls` table
(`schema.sql`) recording every call's SID, caller, source (`twilio` or
`test_console`), and start/end time; the full transcript is bulk-written to
`call_transcripts` once each call ends (`app/bot.py`'s
`on_client_disconnected`), not per-turn, so this adds zero latency to the
live audio loop.

**Browser test console** (`test_console_bot.py`) — talk to Vedonica from a
browser tab's mic instead of dialing a real number, without giving up
DB-backed tools like `local_run.py` does (it still needs `DATABASE_URL` to
resolve orders — see its docstring for the fallback behavior). It reuses
the *exact* pipeline `app/bot.py` builds for real calls (`build_bot_pipeline`
is shared between the two), so what you hear here is what a caller hears.
Run it as its own process, on a different port than the main server:

```
python test_console_bot.py -t webrtc --port 7861
```

Then open `http://localhost:7861/client`, allow mic access, and talk. This
uses Pipecat's built-in **development runner** — explicitly a local dev
tool (unauthenticated, no rate limiting) — so keep it off the public
internet, same as `local_run.py`. The dashboard's "Test console" tab just
links to that URL. Every session shows up in the dashboard's Calls list
tagged `test_console`.

## Setup

1. `python -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. `cp .env.example .env` and fill in: Twilio, Deepgram, OpenAI, Cartesia,
   Postgres, CRM/Calendar API keys.
4. Create the DB: `psql $DATABASE_URL -f schema.sql`
5. Run locally + tunnel:
   ```
   uvicorn server:app --reload --port 7860
   ngrok http 7860
   ```
6. In the Twilio Console → your phone number → Voice Configuration → "A
   call comes in" → Webhook → `https://<ngrok-id>.ngrok.io/voice` (HTTP
   POST).
7. Call the number.

## Local testing on your mic/speakers (no Twilio at all)

`local_run.py` runs the exact same pipeline as `app/bot.py`
(STT → Conversation Manager → LLM/Tools → TTS) but swaps the Telephony/Twilio
box for Pipecat's `LocalAudioTransport`, which talks to your computer's
default microphone and speakers directly — no server, no ngrok, no phone
number, no WebSocket.

1. Install PortAudio (PyAudio's native dependency):
   - macOS: `brew install portaudio`
   - Linux: `sudo apt-get install portaudio19-dev`
   - Windows: no extra step, a prebuilt wheel is used.
2. `pip install -r requirements.txt` (already includes the `local` extra).
3. `cp .env.example .env` and fill in **just** the STT/LLM/TTS keys you plan
   to use (Deepgram + OpenAI + Cartesia by default). You can leave
   `TWILIO_*`, `DATABASE_URL`, `CRM_*`, `CALENDAR_*` blank — the script
   still runs; DB-backed tools (`check_order_status`, tickets, etc.) will
   just return a soft `{"error": ...}` if Postgres isn't configured.
4. `python local_run.py`
5. Put on headphones (so the bot's own voice out of your speakers doesn't
   get picked up by your mic and cause it to interrupt itself) and start
   talking. Ctrl+C to stop.

Everything else — provider swaps via `.env`, `allow_interruptions`,
`enable_metrics`, the Vedonica prompt/persona, tool calls — behaves
identically to the real phone flow, since it's the same pipeline code.

## Conversation memory (temporary) and token-usage cap

The bot remembers everything said earlier **in the current call only** —
nothing is saved to disk or any DB, and a new call starts with a blank
slate. That memory works by resending the transcript to the LLM on every
turn (this is just how stateless chat APIs work — there's no way for the
model to "remember" without it). What you saw in the terminal — the
request payload growing every turn — is that transcript, and yes, it gets
expensive on a long call if left unbounded.

That's now capped via Pipecat's built-in auto context summarization
(wired in `app/config.py` → `build_assistant_aggregator_params()`, used by
both `app/bot.py` and `local_run.py`): once the conversation crosses
either threshold below, older turns are collapsed into one short
LLM-written summary instead of being resent verbatim forever.

```
CONTEXT_MAX_TOKENS=3000            # summarize once context roughly hits this many tokens
CONTEXT_MAX_MESSAGES=12            # ...or once this many new messages have piled up, whichever first
CONTEXT_SUMMARY_TOKENS=600         # cap on the summary that replaces old turns
CONTEXT_MIN_RECENT_MESSAGES=4      # most recent messages always kept verbatim, never summarized
```

After a trigger, the context becomes `[system prompt] + [summary of older
turns] + [most recent messages]` — so the LLM still has the gist of
everything (order id mentioned 8 turns ago, the original complaint, etc.)
without paying full token price for the exact wording of every past turn.
Tune the numbers down (e.g. `CONTEXT_MAX_MESSAGES=8`) if you want it to
summarize more aggressively and cut cost further; tune up if you notice
the bot losing track of details it should still remember.



Go to the Cartesia Voice Library, filter for Hindi/multilingual voices (or
clone one for the Vedonica persona), and drop its ID into
`CARTESIA_VOICE_ID`. Test with real Hinglish sentences, not just English —
some multilingual voices are stronger on one language than the other.

## Knowledge base (products, policies, T&Cs, About Us, FAQ) — token optimization

This content used to live directly inside `VEDONICA_SYSTEM_PROMPT`, which
meant it was sent to the LLM on every single turn of every single call,
whether or not the customer ever asked about it — and the prompt (and cost)
grew every time you added a product or FAQ entry.

It now lives in `app/knowledge_data.py` (plain Python data — edit freely,
no code changes needed elsewhere) and is served through a
`search_knowledge_base` tool (`app/tools.py` + `app/knowledge_base.py`)
that the LLM calls on demand. The system prompt just tells it to call the
tool for these questions instead of guessing; the actual catalog/policy
text is only pulled in on the turn it's needed, as a small tool result
(~30–80 words) instead of a permanent multi-hundred-token prompt fixture.

Search is a simple in-memory keyword-overlap match (no vector DB, no
embedding API call, no added latency) — plenty for a catalog this size. If
you grow into hundreds of products/FAQ entries, swap the scoring logic in
`knowledge_base.py`'s `search()` for a real vector search; the tool
interface doesn't need to change, so nothing else in the codebase does
either.

To add or edit a product, policy, or FAQ answer: just edit the lists/dicts
in `app/knowledge_data.py`.

## Swapping pieces

Provider swaps for STT/LLM/TTS are now just `.env` changes — see the
multi-provider section above. `app/config.py` is the single place that
would need a new `elif` branch if you want a provider not already listed
(e.g. a self-hosted XTTS/Whisper endpoint).

## Production notes

- Deploy behind a real TLS domain (not raw ngrok) for `PUBLIC_HOSTNAME`.
- Put `server.py` behind a process manager (e.g. `uvicorn` with multiple
  workers behind a load balancer, or Pipecat Cloud) — each call holds one
  long-lived WebSocket + pipeline, so plan capacity per concurrent call,
  not per request.
- `call_transcripts` table is there for QA/analytics — wire
  `db.log_call_transcript_turn` into the context aggregator callbacks if
  you want every turn logged (kept out of the hot path by default to avoid
  adding DB latency to the audio loop).
- Add a Twilio status-callback webhook to reconcile call records if a
  socket drops mid-call.
