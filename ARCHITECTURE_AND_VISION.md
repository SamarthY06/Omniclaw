# Architecture & Vision — Ben / Jarvis Multi-Device Personal Assistant

> **Status:** Living design document, May 2026. Pairs with `GOAL.md` (the problem
> statement), `USE_CASES.md` (the full task surface), `BEN_ANDROID_SETUP.md`
> (the build/install guide), and `omniclaw/README.md` + `omniclaw/INSTALL.md`
> (the Mac side). Read those first if you haven't.
>
> This document covers four things:
>
> 1. **The vision** — what we are building and why no off-the-shelf system covers it.
> 2. **The current implementation** — exactly what is in the repo today,
>    on Mac (`omniclaw/`) and on Android (`android/`).
> 3. **The industry landscape** — detailed analysis of three reference repos
>    (`HKUDS/AI-Trader`, `vercel-labs/agent-browser`, `tinyhumansai/openhuman`)
>    so we know what is already solved, what we should steal, and what we are
>    uniquely doing.
> 4. **The target architecture** — where this codebase should be in 90 days,
>    with honest trade-offs, drawbacks, and a stepwise migration plan.

---

## Table of contents

1. [Vision](#1-vision)
2. [Current implementation](#2-current-implementation)
3. [Industry landscape — three reference repos](#3-industry-landscape--three-reference-repos)
4. [What makes this project unique](#4-what-makes-this-project-unique)
5. [Detailed technical approach](#5-detailed-technical-approach)
6. [Honest assessment — drawbacks & trade-offs](#6-honest-assessment--drawbacks--trade-offs)
7. [Target architecture (the pivot)](#7-target-architecture-the-pivot)
8. [Roadmap — next 90 days](#8-roadmap--next-90-days)
9. [Appendix — references, dependencies, glossary](#9-appendix)

---

## 1. Vision

### 1.1 Problem statement (one paragraph)

Build a personal assistant that behaves like Jarvis: one assistant, one wake
word, **one app installed on each of your devices** (Mac, Android, later
iOS), that you talk to in plain language and that **actually drives the apps
you already use** to get things done. Not a chatbot. Not a smart speaker.
Not a vendor-locked voice shell with a fixed command vocabulary. A real
agent that opens Outlook on your Mac, drafts a reply to John, then asks the
Mac to send the offer-letter PDF to the phone, which opens WhatsApp on the
phone, finds Dad, attaches it, and sends — all from a single sentence,
without you scripting the handoff.

### 1.2 The form factor — peer mesh, not central hub

We deliberately reject the "Mac is the brain, phone is a passive remote"
architecture. The design is a **peer mesh of autonomous device-agents**:

```
   ┌──────────────────────┐                ┌──────────────────────┐
   │   Mac agent (Ben)    │                │  Phone agent (Ben)   │
   │                      │                │                      │
   │  • Full ReAct loop   │  WSS + mTLS    │  • Full ReAct loop   │
   │  • Tool registry     │ ───────────────┤  • Tool registry     │
   │  • Local memory      │  HMAC-paired   │  • Local memory      │
   │  • Vision pipeline   │  JSON-RPC 2.0  │  • Vision pipeline   │
   │  • Voice + wake      │  Tailscale     │  • Voice + wake      │
   │  • macOS AX driver   │  off-LAN       │  • Android AX driver │
   │                      │                │                      │
   │  Drives: native Mac  │                │  Drives: native     │
   │  apps + real browser │                │  Android apps       │
   │  via Playwright/CDP  │                │  (Swiggy, UPI,      │
   │                      │                │  WhatsApp, etc.)    │
   └──────────────────────┘                └──────────────────────┘
              │                                       │
              └──────── Wake arbiter ─────────────────┘
                 (only one device responds per utterance)
```

Each device:

- Has its own complete agent brain. Neither asks the other for permission to think.
- Runs 24/7 as a background service (launchd on Mac, FGS + AccessibilityService on Android).
- Listens for the wake word **on-device** (no audio leaves the device pre-wake).
- Talks to the peer only when a task genuinely needs the other device.

No central server. No required hub. No SaaS subscription. Just two (eventually three) processes that find each other and cooperate.

### 1.3 The five non-negotiable properties

| # | Property | Concretely means |
|---|----------|------------------|
| 1 | **Always-on, background, autonomous** | Survives reboot, screen-off, battery-saver. Wakes to wake-word and to scheduled triggers without me opening the app. |
| 2 | **Voice-first, hands-free** | Wake word triggers one device only. Natural speech in, brief useful speech out. Interruptible mid-task. |
| 3 | **Per-device autonomy, peer coordination** | Phone task → phone executes alone (Mac off is fine). Mac task → Mac executes alone (phone off is fine). Cross-device handoff is invisible to the user. |
| 4 | **BYOK (bring-your-own-key)** | User plugs in their own OpenAI / Anthropic / etc. key. No required subscription. No SaaS middleman. Switch providers without rewriting config. |
| 5 | **Safety by sensitivity classification** | Every action is S0/S1/S2/S3. S3 (passwords, OTP, payments, biometrics) is *never* auto-executed — the agent navigates to the screen and hands it back to the user. |

These properties are upstream of every implementation decision in this doc.

### 1.4 Why no existing system covers this

- **Siri / Google Assistant / Alexa** — narrow command vocabulary, vendor-locked, cloud-required, cannot navigate arbitrary apps, cannot compose contextual messages, cannot coordinate across devices, cannot be customized with your own LLM key.
- **ChatGPT / Claude desktop apps** — chatbots, not agents. They cannot actually open Swiggy and place an order. They cannot drive UI. They have no peer model.
- **OpenInterpreter, Open WebUI, AnythingLLM** — Mac/desktop only, no Android, no peer coordination, no wake-word, no native-app driving.
- **OpenHuman (TinyHumans)** — Mac/Linux/Windows daemon with extensive integrations, but no Android client, no peer mesh, and routes inference through its own backend. We analyze this in detail in §3.3.
- **AppAgent (Tencent), Mobile-Agent (Alibaba), AutoDroid (THU)** — academic research papers, no shipped APK, no Mac peer.
- **Rabbit R1, Humane Pin** — failed hardware; tried to solve a slice of this.

The assistant we want **sits in the gap none of those fill**.

---

## 2. Current implementation

This section documents what is in the repository right now (May 2026), with
file references and concrete details.

### 2.1 Overall stack — both sides at a glance

| Plane | Mac (`omniclaw/`) | Android (`android/`) |
|---|---|---|
| Brain (agent runtime) | OpenClaw npm CLI (`openclaw@latest`) invoked from the Python daemon | `openclaw@^2026.5.6` running inside an **embedded `nodejs-mobile`** Node 18 runtime (`libnode.so` shipped per ABI, ~85 MB APK) |
| Native UI driver (AX layer) | `omniclaw/tools/macos_accessibility.py` — pyobjc + AXUIElement API; indexed-element registry; coordinate-based CGEvent clicks | `BenAccessibilityService.kt` — `AccessibilityNodeInfo` tree; stable node-id cache; `GestureDescription` taps + `ACTION_SET_TEXT` |
| AX CLI shim | `omniclaw/tools/macos_ax.py` — JSON-out CLI (`tree`, `click`, `type`, `screenshot`, `launch`, etc.) | Kotlin `JsonRpcServer` on :18792 exposing the same surface |
| Vision fallback | `omniclaw/tools/macos_vision.py` — screenshot + gpt-4o vision for coordinate identification when AX tree is empty | `AndroidOcr.kt` (on-device ML Kit text-recognition) + `vision.read_screen` (cloud gpt-4o, S2 — screenshot leaves device) |
| Web driver | OpenClaw's built-in **Playwright/CDP** browser tool — drives the user's real browser | Not used; phone is native-app-only |
| Voice (wake word) | `omniclaw/voice/wakeword_mac.py` — `SFSpeechRecognizer(requiresOnDeviceRecognition=True)` + fuzzy phrase matcher; launchd plist `ai.ben.wakeword.plist` | `BenWakewordService.kt` — Android `SpeechRecognizer` + `WakePhraseMatcher.kt` fuzzy match |
| Voice (post-wake conversation) | OpenClaw Talk Mode → OpenAI Realtime WSS | `BenVoiceService.kt` → OpenAI Realtime WSS directly |
| Peer protocol | `omniclaw/peer/{daemon,server,client,pair,discovery}.py` — WSS :18790, Unix-socket control plane `~/.jarvis/peer.sock`, HMAC-signed JSON-RPC, mDNS for LAN discovery | `assets/node/src/peer/*.js` — **wire-compatible JS port** of the same protocol, embedded inside the APK's Node runtime |
| Off-LAN transport | **Tailscale** (managed installation, `brew install --cask tailscale`) | Tailscale Android client |
| Wake arbiter | `omniclaw/wake/arbiter.py` — UDP multicast on a private port; "only one device responds" when both hear the wake word | Same protocol on the phone |
| Secrets / keychain | macOS Keychain via `security` CLI (`security add-generic-password -s jarvis`) | `BenSecrets.kt` + AndroidX `EncryptedSharedPreferences` |
| Persistence | `~/.openclaw/workspace/` — `memory.json` (KV), `USER.md` (hand-curated facts), `AGENTS.md`, `SOUL.md`, `TOOLS.md`, JSONL session transcripts | `/data/data/com.ben/files/openclaw/workspace/` — same layout |
| Process supervision | launchd: `ai.jarvis.peer.plist` (peer daemon, KeepAlive=true) + `ai.ben.wakeword.plist` (wake listener) | `BenForegroundService` (mic + specialUse FGS) hosts `BenWakewordService` + `BenVoiceService` + `NodeBridgeService` |
| Distribution | Python `.venv` + npm `openclaw` install + manual launchd plist sed-templating | `./scripts/bootstrap.sh` → `assembleDebug` → `~/Desktop/Ben.apk` → QR/Drive/`adb install` sideload |

### 2.2 Tool surface exposed to the LLM (today)

Both sides expose the same conceptual tools so the model's mental model is
identical regardless of which device it's running on:

```
peer.delegate(task)            — hand a natural-language task to the paired device

device.get_location()          — last-known GPS / network fix             (S1)
device.get_contacts(query?)    — address-book search                       (S1)
device.place_call(number|name) — Intent.ACTION_CALL / Mac equivalent       (S1)
device.launch_app(label)       — resolve label → bundle/package → launch   (S0)
device.clipboard_get/set       — read/write clipboard                      (S0)
device.battery_status          — pct, charging, source                     (S0)
device.set_alarm/timer/...     — AlarmClock intent / Mac equivalent        (S0/S1)
device.add_calendar_event(...) — open prefilled "new event" form           (S0)

ui.focus_app(pkg)              — foreground an app                         (S0)
ui.read_screen()               — dump AccessibilityNode tree (≤200 nodes)  (S0)
ui.click({text|ax_id})         — tap by visible text or AX id              (S1)
ui.click_at(x, y)              — tap pixel coords (Compose / WebView)      (S1)
ui.type(text)                  — type into focused field                   (S1)
ui.scroll(direction)           — page-style scroll                         (S0)
ui.swipe(x1,y1,x2,y2)          — free-form gesture                         (S0)
ui.screenshot()                — full-screen PNG via MediaProjection / CG  (S0)
ui.screen_size()               — pixel dimensions                          (S0)

vision.locate_text(target)     — on-device OCR, returns click coords       (S0)
vision.read_screen(question)   — screenshot + question → gpt-4o (cloud!)   (S2)

web.fetch(url, ...)            — generic HTTPS, body ≤ 64 KB               (S2)
weather.current(loc?)          — wttr.in (no API key)                      (S2)

memory.set/get/search/         — durable on-device KV store (memory.json)  (S0)
        list/delete
memory.user_facts              — read USER.md                              (S0)
memory.append_user_facts(...)  — append to USER.md                         (S0)
```

Mac additionally exposes the full `macos_ax.py` command set (launch, focus,
quit, list-apps, focused-app, list-windows, double-click, right-click,
shortcut, drag, hover) and the OpenClaw browser tool (Playwright/CDP) for
real-browser tasks.

### 2.3 Wake → respond → coordinate flow

```
   User says: "Ben, send Pragati on WhatsApp 'on my way'"
       │
       ▼
   Mac wakeword.plist hears "Ben"   Phone WakewordService hears "Ben"
              │                             │
              └─────── UDP multicast ───────┘
                    Wake Arbiter
              │                             │
              ▼                             ▼
       Mac priority=10                Phone priority=??
       Phone closer (RSSI / dB)? → Phone wins
              │                             │
              ▼                             ▼
       Mac stays silent               Phone starts Realtime session
                                            │
                                            ▼
                                     LLM: this is a WhatsApp/Send task
                                     → use AX driver locally
                                            │
                                            ▼
                                     ui.focus_app("com.whatsapp")
                                     ui.read_screen()  → AX tree
                                     ui.click(text="Pragati")
                                     ui.click(ax_id="message_field")
                                     ui.type("on my way")
                                     ui.click(ax_id="send_button")
                                            │
                                            ▼
                                     Brief TTS: "Sent."
```

Cross-device example:

```
   On Mac: "Ben, send the offer letter on my Desktop to Dad on WhatsApp"
       │
       ▼
   Mac handles the speech; LLM realizes WhatsApp is a phone task
       │
       ▼
   peer.delegate(task: "send /Users/.../offer.pdf to Dad on WhatsApp")
       │ JSON-RPC over WSS :18790 + HMAC + (Tailscale if off-LAN)
       ▼
   Phone receives delegate; spins up its own Realtime session silently
       │
       ▼
   Phone: ui.focus_app("WhatsApp"), find Dad, attach the file
   (file was streamed over the peer link in chunks during delegate)
       │
       ▼
   Phone returns {ok: true, status: "sent"}
       │
       ▼
   Mac speaks: "Sent the offer letter to Dad."
```

### 2.4 Sensitivity model

Already implemented across the tool surface, classified in the LLM's system
prompt and enforced at the tool layer:

| Level | Name | Behavior | Example tools / actions |
|---|---|---|---|
| **S0** | Safe | Execute immediately, brief confirmation | open app, scroll, search, read screen, OCR, alarm, timer |
| **S1** | Reversible | Execute immediately, notify on completion | add to cart, bookmark, save draft, place call, save photo |
| **S2** | Important | Confirm in chat/voice before acting; data leaves device | send message, order food, post comment, web.fetch, vision.read_screen |
| **S3** | Sensitive | **Never auto-execute.** Navigate to the screen and hand off to the user | payment, OTP, password, card number, account deletion, government ID, biometrics |

S3 fields ("radioactive") are *never* read into memory, *never* logged,
*never* sent to any model. The AX serializer should redact any node where
`isPassword=true` / `secureTextEntry=true` *before* returning the tree to
the LLM. (This rule is documented; we need to audit that it's actually
enforced in every code path — see §6.)

### 2.5 Current memory model

```
~/.openclaw/workspace/                    /data/data/com.ben/files/openclaw/workspace/
├── AGENTS.md           ← system prompt + operating instructions
├── SOUL.md             ← persona, tone, naming
├── USER.md             ← hand-curated facts (contacts, addresses, defaults)
├── TOOLS.md            ← tool descriptions for the LLM
├── memory.json         ← KV store, atomic writes, in-process cache
└── sessions/
    └── 2026-05-12T03-11-12.jsonl   ← append-only transcript
```

`memory.*` tools provide set/get/search/list/delete with substring search
across keys+values. Session JSONL is pruned by simple retention rules
(currently: keep 30 days). There is no vector store, no FTS5, no
hierarchical summary tree — just KV + flat session logs.

---

## 3. Industry landscape — three reference repos

To know whether we're reinventing wheels and which patterns to steal, we
did deep code-level analyses of three open-source projects.

### 3.1 HKUDS/AI-Trader — agent-native trading platform

**URL:** https://github.com/HKUDS/AI-Trader  
**Relevance to us:** Low — different domain (paper trading) — but it
introduced two patterns worth borrowing.

**What it actually is.** A FastAPI + React platform where external LLM
agents (Claude Code, Cursor, etc.) interact with a paper-trading server
over HTTP. The platform is "agent-native" — instead of being a bot that
trades for you, it's a server with an HTTP API that LLMs are taught to
call. The autonomy lives in the LLM, not in the platform.

**Stack.** FastAPI + Uvicorn + Pydantic v2 + PostgreSQL/SQLite (with
runtime SQL translation) + Redis (optional cache, graceful fallback) +
React 18 + Vite + Recharts. Background tasks run in a *separate* `worker.py`
process via 11 asyncio loops (price refresh, settlements, market intel,
profit-history pruning, etc.).

**Patterns we should steal.**

1. **Agent-native via skill files.** Each capability exposed to LLMs is
   documented as a `SKILL.md` with frontmatter + Markdown body
   (`skills/ai4trade/SKILL.md`, `skills/heartbeat/SKILL.md`,
   `skills/tradesync/SKILL.md`, etc.). The LLM reads `SKILL.md` at session
   start and learns the API. We already do this; AI-Trader confirms the
   pattern scales.
2. **Worker process separated from RPC.** Don't run cron, ingestion, and
   the JSON-RPC handler in the same event loop. Our peer daemon today does
   both; we should split it.
3. **Tiered retention.** AI-Trader keeps 24 h profit data full-resolution,
   then 7 d at 15-min, 30 d hourly, 365 d daily. Apply the same to our
   session JSONLs and screenshots: keep last 24 h raw, summaries thereafter.

**Patterns we should not borrow.** It's API-driven not GUI-driven, so its
trading specifics don't transfer.

### 3.2 vercel-labs/agent-browser — Playwright for LLMs in Rust

**URL:** https://github.com/vercel-labs/agent-browser  
**Relevance to us:** Medium — different scope (web only) — but its
*architecture* is exactly the pattern we should adopt for the Rust core
we recommend in §7.

**What it actually is.** A single CLI (`agent-browser`) that lets LLMs
drive Chrome. Distributed as an npm package that's really a 121-line
Node.js shim around a **native Rust binary**. The Node shim spawns a
persistent **Rust daemon** that owns the Chrome session, talks to it over
**Chrome DevTools Protocol** (WebSocket JSON-RPC), and exposes accessibility
tree snapshots back to the LLM with token-efficient `@eN` element refs.

**Stack.** Rust (tokio, tokio-tungstenite, reqwest, aes-gcm, image,
rust-embed) + auto-generated CDP types from `cdp-protocol/*.json` at build
time + Node.js shim (`bin/agent-browser.js`) + Next.js 16 dashboard
embedded via `rust-embed`.

**The architecture you'd diagram on a whiteboard.**

```
┌───────────────────┐
│  npm shim (Node)  │  ← 121 lines, just spawns the binary
└────────┬──────────┘
         │  Unix socket (macOS/Linux) or TCP (Windows)
         ▼
┌───────────────────┐
│  Rust daemon      │  ← lifetime spans many CLI invocations
│  (single binary)  │
│                   │
│  • Chrome process │
│  • CDP client     │
│  • Snapshot       │
│    accessibility  │
│    tree → @eN     │
│  • Encrypted      │
│    state (cookies,│
│    local storage) │
│  • Skills via     │
│    rust-embed     │
│  • Dashboard via  │
│    rust-embed     │
└────────┬──────────┘
         │  WebSocket CDP
         ▼
   Chrome / Chromium
```

**The seven ideas worth lifting.**

1. **Trait-based `BrowserBackend` abstraction.** One trait, multiple
   implementations: CDP (Chrome), WebDriver (Safari/iOS via Appium),
   Lightpanda (pure-Rust headless). The agent reasons about an abstract
   surface; the backend handles platform calls. We rename this to
   `PlatformBackend` and add `MacOsBackend`, `AndroidBackend`, `IosBackend`.
2. **`@eN` accessibility refs.** Instead of feeding the LLM
   `{"role":"Button","label":"Send","bounds":[...]}` per element, give it
   `@e7` and let it click by ref. Saves 30–50× tokens vs verbose JSON
   trees. We already index elements `[1] [2] [3]`; we should adopt the
   `@eN` convention and merge it across Mac + Android + (eventual) iOS.
3. **Build-time codegen from the protocol spec.** Their `build.rs` parses
   `cdp-protocol/*.json` (Chrome's official spec, 35k+ lines) and emits
   type-safe Rust. We can do the same for `accessibility_service_config.xml`
   on Android and the AX role constants on macOS.
4. **Embedded skills + dashboard via `rust-embed`.** Skill files and a
   Next.js dashboard get baked into the Rust binary. Versions can't drift
   between client and runtime. We should do this for `USER.md`,
   `AGENTS.md`, `TOOLS.md`, and the per-skill `SKILL.md`s.
5. **Persistent daemon, ephemeral CLI.** The CLI is a thin client; the
   real state lives in a long-running daemon. Repeat invocations are fast
   because Chrome stays open. Our peer daemon already does this on Mac
   (`ai.jarvis.peer.plist`); the Android side currently re-bootstraps
   `nodejs-mobile` more than it should.
6. **Cross-platform single binary distribution.** They ship static binaries
   for macOS ARM64/x64, Linux x64/ARM64/musl, Windows x64. Our Rust core
   will compile to all six the same way.
7. **`tokio-tungstenite` for the wire.** Same WebSocket library we should
   use for the peer protocol — well-tested, async, supports rustls TLS.

**The honest non-fit.** agent-browser is **web only**. It cannot drive
Outlook desktop, Swiggy on Android, or any non-Chromium app. So while we
borrow its architecture pattern, we don't ship it as a dependency.

### 3.3 tinyhumansai/openhuman — Tauri-based desktop AI assistant

**URL:** https://github.com/tinyhumansai/openhuman  
**Relevance to us:** High — this is roughly **80% of the Mac assistant we
are building**, plus extensive integrations, in Rust, with a daemon that's
already cross-platform-compilable. Critical context for "are we reinventing
the wheel?"

**What it actually is.** A Tauri 2 desktop app (Windows/macOS/Linux) +
Rust core daemon, packaged as a single installer. Pitches itself as a
"personal AI super intelligence" — a local-first Karpathy-style LLM
knowledgebase + Composio-style 118+ OAuth integrations + agentic mascot
that joins Google Meets as a participant. Can also run **headless in
Docker** for VPS deployment.

**The codebase by numbers.** 2,799 files. ~38 MB uncompressed source.
59 first-class business-logic domains in `src/openhuman/`. Two vendored
Tauri forks (`tauri-cef`, `tauri-plugin-notification`) pinning to a CEF
Chromium runtime. ≥80% diff-coverage gate enforced in CI. Three independent
Sentry projects (frontend, Tauri shell, Rust core), each with secret-
scrubbing `before_send`.

**The 5-process runtime.**

```
┌──────────────────────┐      ┌──────────────────────┐      ┌────────────────────┐
│  React 19 (Vite)     │ IPC  │  Tauri shell         │ TCP+ │  openhuman-core    │
│  inside CEF Chromium │ ◄──► │  (Rust, CEF runtime) │ JWT  │  daemon (Rust)     │
└──────────────────────┘      └──────────┬───────────┘      └─────────┬──────────┘
                                         │                            │
                                         │ CDP on :9222               │ Axum /rpc :7788
                                         │ (scrapes 3rd-party web     │ Socket.IO server
                                         │  apps inside the same CEF) │ OTLP/Sentry
                                         ▼                            │
                              Gmail / Slack / Meet /                  ▼
                              WhatsApp / iMessage db          api.tinyhumans.ai
                                                              (LLM, Composio,
                                                               ElevenLabs, OAuth)
```

**The 59 domains, grouped.**

| Group | Domains | What they do |
|---|---|---|
| Agent runtime | `agent/`, `subconscious/`, `heartbeat/`, `approval/`, `prompt_injection/` | ReAct loop, between-turn "always thinking" loop, scheduler, S0/S1/S2/S3 approval gates, prompt-injection guard |
| Memory | `memory/`, `memory/tree/`, `embeddings/`, `tree_summarizer/`, `learning/` | Hybrid (vector + FTS5) search; Obsidian-vault Markdown chunks; per-source/per-topic/global summary trees; post-turn self-learning |
| LLM transport | `providers/` (compatible, router, reliable, openhuman_backend) | OpenAI-compatible transport, hint-based model routing (`hint:reasoning/fast/vision`), retry + circuit breaker |
| Channels | `channels/providers/` × 18 | DingTalk, Discord, Email, iMessage, IRC, Lark, Linq, Matrix, Mattermost, QQ, Signal, Slack, Telegram, WhatsApp (Bot API + Web E2EE), Web, etc. |
| Integrations | `composio/`, `integrations/` | Backend-proxied 1000+ Composio tools + direct Apify / Parallel.ai / Google Places / Stock prices / Twilio |
| Voice + meet | `voice/`, `meet/`, `meet_agent/` | `whisper-rs` (Metal-accelerated), Piper TTS, full Google Meet participant pipeline (VAD-segmented STT → LLM → ElevenLabs TTS, with the mascot SVG fed in as the meeting webcam via CEF `--use-file-for-fake-video-capture`) |
| Automation | `cron/`, `webhooks/`, `redirect_links/`, `referral/` | tokio-driven 5-second-tick scheduler, SQLite-stored jobs, shell-job + agent-job types |
| Tools (LLM surface) | `tools/impl/` × 9 categories | agent (delegate, spawn subagent, todo_write…), browser (wraps agent-browser!), computer (enigo/rdev), cron, filesystem, memory, network, system, whatsapp_data |
| Skills | `skills/` | Markdown skills parsed from User/Project/Legacy scopes, matched per-turn, injected with an 8 KiB cap |
| Local AI | `local_ai/` | Bundled Ollama + whisper.cpp + Piper, tier-based model presets (Low/Medium/High × Vision) |
| Resource gating | `scheduler_gate/` | `Policy::{Aggressive, Normal, Throttled, Paused}`, sampled every 30s (AC/battery/CPU); background tasks `await wait_for_capacity()` before consuming LLM/embedding compute |
| Webview scraping | `webview_accounts/`, `webview_apis/`, `webview_notifications/`, `whatsapp_data/` + per-service scanners (`discord_scanner/`, `gmessages_scanner/`, `imessage_scanner/`, etc.) | The "auto-fetch" loop — every 20 min, the core walks each active CEF tab via CDP and ingests Gmail/Slack/Discord/iMessage data into the memory tree |
| Other | `wallet/`, `team/`, `billing/`, `cost/`, `referral/`, `service/`, `update/`, `health/`, `doctor/` | Crypto wallet (non-custodial), commercial concerns, native service installer (launchd/systemd/Windows-service), auto-update, health checks |

**The subconscious loop (their answer to "the assistant keeps thinking").**

```rust
// src/openhuman/subconscious/engine.rs
// On each tick: load due tasks from SQLite → log as in_progress →
// evaluate with local model → execute "act" tasks → create escalations
// for ambiguous tasks → update log entries in place.
//
// Overlap guard: each tick gets a generation counter. If a new tick
// starts while the old one is in-flight, the old tick's in_progress
// entries are marked as cancelled and its results are discarded.
```

User-editable task list lives in `~/.openhuman/HEARTBEAT.md`. The heartbeat
engine (≥5-min intervals) reads this file, runs the situation report (current
workspace state + recent reflections + relevant memory chunks), passes
through a **local Ollama model** for evaluation, then either:

- executes the "act" task,
- creates an `Escalation` with priority/status for the user to resolve in
  the UI, or
- writes a `Reflection` to the reflection store (capped per tick).

**This is exactly the "It runs always, even when I'm not using it"
section of our `GOAL.md` §1.5.** They shipped it.

**Memory tree pipeline.**

```
source adapters (chat / email / document)
       │
       ▼
canonicalize/    ← normalised Markdown + provenance Metadata
       │
       ▼
chunker.rs       ← deterministic IDs, ≤3k-token bounded segments
       │
       ▼
content_store/   ← atomic .md files on disk (body + tags)
       │           in an Obsidian-compatible vault layout
       ▼
store.rs         ← SQLite (chunks, scores, summaries, jobs, hotness)
       │
       ▼
score/           ← signals + embeddings + entity extraction
       │
       ▼
tree_source/, tree_topic/, tree_global/   ← per-scope summary trees
       │
       ▼
retrieval/       ← search / drill_down / topic / global / fetch
       │
       ▼
jobs/            ← background workers + scheduler (extract, admit,
                   seal, digest)
```

Everything you see in your inbox, your Slack, your GitHub issues,
your Notion ends up as Markdown files in `~/.openhuman/vault/` that you
can open in **Obsidian** and edit directly. This is the Karpathy
"obsidian-wiki" workflow.

**TokenJuice — tool output compaction.**

`src/openhuman/tokenjuice/` is a Rust port of `vincentkoc/tokenjuice` —
it compacts verbose tool output (git, npm, cargo, docker, scrape
results, email bodies, search payloads) before it touches an LLM
context window. Three-layer rule overlay (builtin via `include_str!`
→ user at `~/.config/tokenjuice/rules/` → project at `.tokenjuice/rules/`).
README claims **80%** token reduction on real-world tool spam.

**What OpenHuman does NOT do (and why this matters for us).**

| Capability | OpenHuman | Why this matters for our project |
|---|---|---|
| Drive **native** Mac apps via AX | **No.** Their `accessibility/` module is a *passive* observer (focused-text context, screen capture, paste). It does not "click the Send button in Outlook by AX id." | We do this. It's unique. |
| Drive **native** Android apps | **No.** No Android client exists. | We do this. Genuinely novel. |
| Drive **the user's real browser** with the user's real session | **No.** Their browser tool wraps agent-browser, which opens its **own isolated Chromium**. | We do this (via OpenClaw's Playwright/CDP). Better UX. |
| Peer-to-peer mesh between user devices | **No.** Single-host architecture only. | We do this. |
| BYOK | **No.** Everything routes through `api.tinyhumans.ai`; LLM calls, OAuth, Composio, ElevenLabs, web search all go through their backend. | We do this. Non-negotiable. |
| Wake-word activated | **No.** Reverse-keyboard-shortcut and tray-click only. | We do this. |
| Apps without an API (Swiggy, Zomato, IRCTC, UPI, PhonePe, government portals) | **No.** Their entire integration model assumes Composio + OAuth APIs exist. | We do this. India-specific killer feature. |

**What we should steal from OpenHuman wholesale.**

| Module | What it gives us | Effort to port |
|---|---|---|
| `memory/tree/` | Obsidian-vault knowledge base with deterministic chunking, hybrid search, summary trees | 2–3 weeks |
| `tokenjuice/` | 80% cost reduction on tool output | 1 week |
| `scheduler_gate/` | Battery-aware throttling — mandatory for Android | 3 days |
| `subconscious/` + `heartbeat/` + `HEARTBEAT.md` | The "always thinking" loop for standing-order tasks | 1–2 weeks |
| `approval/` + sensitivity model | Already aligns with our S0/S1/S2/S3 | 2 days (mostly adoption) |
| `service/{macos,linux,windows}/` | Native service installer that replaces sed-templated launchd plists | 3 days |
| `providers/router.rs` (hint-based) | One LLM gateway that routes `hint:reasoning`, `hint:fast`, `hint:vision` to different models | 4 days |
| Sentry `before_send` secret scrubbing | Telemetry without leaking | 1 day |

**What we explicitly do NOT take from OpenHuman.**

- The CEF Chromium runtime. Overkill for our use case; we don't run a heavy
  UI inside Chromium.
- The 59-domain monolith. We don't need 18 chat-channel providers.
- The "everything goes through `api.tinyhumans.ai`" model. Violates BYOK.
- The Composio integration tier (for now). We may add this later as an
  *opportunistic* path for office-stack apps that have APIs (Gmail,
  Outlook, Calendar, GitHub, Slack), but always alongside AX, never replacing it.

---

## 4. What makes this project unique

Stack-ranked from most to least differentiated.

### 4.1 AX-driven native app control, on every device

Our **single biggest** unique value. Both Mac and Android can read the
accessibility tree of any native app, identify elements by id/role/label,
and synthesize taps + text input. This works for:

- Apps with no public API (Swiggy, Zomato, Ola, BookMyShow, IRCTC, UPI
  apps, PhonePe, banking apps, government portals, internal enterprise
  apps).
- Apps where you specifically want the GUI flow (Outlook, Teams, Slack,
  Notes, Finder) so you see what's happening on your screen.
- The user's **real, logged-in** browser via Playwright/CDP (preserves
  cookies, extensions, saved cards).

No other open-source project covers all three of these surfaces. OpenHuman
covers the third (real browser) only via agent-browser's sandboxed
Chromium, not the user's actual browser. AppAgent/Mobile-Agent/AutoDroid
do Android only and are research code, not shippable.

### 4.2 Peer-to-peer device mesh, no central hub

Two (eventually three) devices each run a complete agent and talk to
each other when a task crosses devices. No master, no required hub,
no SaaS backend. Tasks degrade gracefully:

- One device unreachable → the device that heard you handles what it can,
  tells you plainly what it cannot.
- Both online, both on LAN → direct WSS over local IP.
- Both online, one off-LAN → Tailscale tunnel (no port forwarding, no
  public exposure).
- LLM unreachable → assistant tells you, queues the task for connectivity
  return.

OpenHuman, Open Interpreter, and friends are all single-host. Apple's
Continuity is multi-device but vendor-locked and not LLM-driven.

### 4.3 BYOK with hard cost ceiling

User plugs in their own OpenAI / Anthropic / Groq / Cartesia / Deepgram
key. No subscription. No middleman. Costs are accounted per-call, shown
in the UI, and capped daily + monthly. When cap hits, assistant pauses
and tells you why.

OpenHuman is the opposite of this — every LLM, TTS, and integration call
goes through their backend's billing.

### 4.4 Vision as fallback, not primary

Our planned **4-layer click cascade** (detailed in §5.1) treats vision as
the last resort, after AX-id, AX-label, and on-device OCR have all failed.
This is the opposite of OpenAI Operator / Anthropic Computer Use, which
treat vision as primary. Implications:

- Average click cost approaches zero (most clicks resolve at layers 1–3,
  which are free).
- Average click latency well under 1 s (vision adds 2–4 s).
- Screen contents stay on-device for almost all interactions, satisfying
  our privacy non-negotiable.

### 4.5 Learned flow replay

After the first successful run of a multi-step task, the recorded
element-id sequence is stored as a "flow." Subsequent invocations replay
the flow at full speed with zero LLM calls, falling back to live
re-planning only when a step's verification fails.

This is in `GOAL.md` §1.5 line 110 ("the second time I order biryani it
doesn't need the LLM at every step"). It is the single highest-leverage
feature for cost and latency — once you've ordered the biryani once,
every subsequent order is ~2 s and ~$0.0001. We have not yet implemented
this; it is the top item in §8.

### 4.6 Wake-word arbiter — only one device responds

When two devices both hear "Ben," they coordinate over the peer link and
exactly one responds (chosen by recency / signal strength / explicit
priority). No double-execution, no stacked answers, no echo.

Not novel in concept (HomePod groups do this) but uniquely applied to
peer-mesh agents in this project.

---

## 5. Detailed technical approach

### 5.1 The click cascade (4 layers)

This is the heart of how the agent identifies "where to click" for any
given intent. Currently we have layers 1, 2, and 5 (with 5 being our
default fallback). The recommended target is to insert 3 and 4.

```
Intent: "click the Send button"
   │
   ▼
─────────────────────────────────────────────────────────────────────
Layer 1: AX-by-id (deterministic)
─────────────────────────────────────────────────────────────────────
   ax_id is in the indexed tree (e.g. @e7)? → click immediately.
   Cost: 0. Latency: <20 ms. Privacy: nothing leaves the device.
   Failure modes: tree dump is stale, app changed UI, ax_id rotated.
   │
   ▼  (if not found)
─────────────────────────────────────────────────────────────────────
Layer 2: AX-by-label (fuzzy match)
─────────────────────────────────────────────────────────────────────
   Search the tree for a node whose label/text matches "Send"
   (case-insensitive substring + role hint "button/link").
   Cost: 0. Latency: <50 ms. Privacy: nothing leaves the device.
   Failure modes: Electron app with empty AX tree, custom Compose,
   internationalized labels we don't have a synonym list for.
   │
   ▼  (if no match)
─────────────────────────────────────────────────────────────────────
Layer 3: On-device OCR (free, fast, private)
─────────────────────────────────────────────────────────────────────
   Take a screenshot. Run on-device OCR (Apple's Vision framework on
   Mac, ML Kit on Android). Locate the word "Send" → returns bounding
   box → click center.
   Cost: 0. Latency: ~150 ms. Privacy: nothing leaves the device.
   Failure modes: the word "Send" isn't literally on screen (icon-only
   button, image-as-text, custom font OCR misses).
   │
   ▼  (if OCR doesn't find it)
─────────────────────────────────────────────────────────────────────
Layer 4: On-device VLM (free, slower, private)              ★ TODO
─────────────────────────────────────────────────────────────────────
   Take a screenshot. Pass to a small on-device vision-language model:
     - Mac (M-series): Qwen2-VL-2B or MiniCPM-V via mlx-vlm or llama.cpp
                       (~300 ms on M2+).
     - Android (recent flagship): Gemma 3n via MediaPipe LLM /
                                  Google AICore (~800 ms – 2 s).
     - iOS 18+: Apple Foundation Models (free, on-device, no key).
   Prompt: "Return JSON: bounding box of the Send button."
   Cost: 0. Privacy: nothing leaves the device.
   Failure modes: rare — small VLM occasionally misjudges by 50 px.
   │
   ▼  (if VLM fails OR isn't available)
─────────────────────────────────────────────────────────────────────
Layer 5: Cloud VLM (paid, last resort)
─────────────────────────────────────────────────────────────────────
   Pass screenshot + question to gpt-4o vision (or Claude 3.5 Sonnet
   vision, or Gemini 2.0 vision — provider chosen by `hint:vision`).
   Cost: ~$0.015–0.03 per call. Latency: 2–4 s.
   Privacy: SCREEN LEAVES THE DEVICE. Logged. Counted toward monthly cap.
   User is shown a small UI badge when this layer fires.
   Failure modes: occasional cloud outage, model misjudgment.
```

**Layer-1 to Layer-2 transitions are invisible. Layer-3 onward each adds
a small "via OCR" / "via vision" badge in the timeline so the user can
see exactly which path the agent took for each click.** Transparency is
debugging.

### 5.2 Coordinate caching / learned-flow replay

```
Recording (first run only):
─────────────────────────────────────
{
  flow_id: "swiggy-order-truffles-biryani",
  app: "in.swiggy.android",
  steps: [
    { layer_used: "ax_id",  selector: "@e3",       verified_by: "screen_hash_after" },
    { layer_used: "ocr",    coord: [540, 1894],    verified_by: "screen_hash_after" },
    { layer_used: "ax_label", selector: "Add",     verified_by: "screen_hash_after" },
    ...
  ],
  ended_at_screen: <hash of AX tree at flow exit>
}

Replay (subsequent runs):
─────────────────────────────────────
For each recorded step:
  1. Take a quick AX-tree dump (<50 ms).
  2. If the screen hash matches the recorded screen hash for this step,
     replay the recorded action (click ax_id / coord / label) blindly.
  3. After the action, dump again. If the resulting screen hash matches
     the *next* step's expected pre-state, continue.
  4. If at any point the hash mismatches, fall through to live re-planning
     with the LLM and update the recording.
```

This is the difference between "Jarvis-feeling" and "fancy but slow."
A repeat task should complete in **2 seconds** with **near-zero LLM cost**.
This is `GOAL.md` Definition of Done #9.

### 5.3 Peer protocol

Wire format: JSON-RPC 2.0 over WebSocket Secure (rustls / OpenSSL).

```
┌────────────────────────────────────────────────────────────────┐
│                  Peer message envelope                         │
├────────────────────────────────────────────────────────────────┤
│  Frame:    WebSocket Binary frame                             │
│  Body:     JSON-RPC 2.0 request/response/notification         │
│  Auth:     HMAC-SHA256 over (id || method || params)          │
│            using 32-byte shared secret from pairing QR        │
│  Replay:   Nonce + monotonic-timestamp, rejected if           │
│            seen within 60 s window                            │
│  Schema:   Versioned in src/proto/types.rs (SCHEMA_VERSION,   │
│            SCHEMA_MIN, SCHEMA_MAX); reject incompatible peers │
└────────────────────────────────────────────────────────────────┘
```

Method namespace:

```
peer.ping                   {ts_ms} → {ok, rtt_ms}
peer.hello                  {schema_version, caps, device_id, priority}
peer.delegate               {task: <natural language>, attachments?, deadline?}
peer.cancel                 {task_id}

ui.read_screen, ui.click, ui.type, ui.swipe, ui.screenshot, ...   (mirror local API)
device.get_location, device.place_call, ...                       (mirror)
fs.stat, fs.read, fs.write, fs.list, fs.transfer                  (file ops)

wake.heard                  {ts_ms, confidence, device_id}   ← arbiter coordination
wake.takeover               {ts_ms, device_id, reason}
```

Pairing (one-time):

```
1. Mac generates `jarvis://pair?secret=<base64>&device_id=mac-xxxx&port=18790`
2. Mac shows a QR code in terminal (qrcode[pil]).
3. Phone scans QR → decodes the URI → calls peer.hello → both sides
   persist peer.json + identity.json + EncryptedSharedPreferences.
4. Subsequent connects use the 32-byte shared secret for HMAC.
```

Off-LAN: Tailscale's MagicDNS gives both devices stable hostnames; the
peer daemon resolves through `tailscale status --json` and dials over the
tailnet without any public port exposure.

### 5.4 Wake arbiter (replacing the current UDP multicast design)

**Current implementation** (`omniclaw/wake/arbiter.py`): UDP multicast on
a private port. When a device hears the wake word, it broadcasts a
`wake.heard` packet; devices then deterministically choose a leader based
on `(priority, ts_ms, device_id)`.

**Problems with UDP multicast** (documented in `BEN_ANDROID_SETUP.md`
troubleshooting): corporate WiFi blocks multicast, public WiFi blocks AP
isolation, eSIM/cellular can't multicast at all, IGMP snooping off by
default on most consumer routers.

**Recommended replacement**: rendezvous **over the existing peer link**.
Each device, on hearing wake, immediately sends a `wake.heard` notification
to the peer over WSS (already-open connection, ~5 ms). Both sides apply
the same tie-breaking rule (`(priority, ts_ms, device_id)`). Deterministic,
works on any network the peer link works on, no extra surface to maintain.

### 5.5 Memory model (current → target)

**Current**:

```
~/.openclaw/workspace/memory.json   (flat KV, atomic writes, ~5–10 KB)
~/.openclaw/workspace/USER.md       (hand-curated facts, read at session start)
~/.openclaw/workspace/sessions/*.jsonl   (append-only transcripts)
```

**Target** (port from OpenHuman's `memory/tree/`):

```
~/.ben/workspace/
├── USER.md                      ← hand-curated, unchanged
├── HEARTBEAT.md                 ← user-editable standing-order tasks
├── memory.json                  ← simple KV stays, for quick facts
├── chunks.db                    ← SQLite: chunks, scores, summaries,
│                                  entity index, hotness, jobs
├── vault/                       ← Obsidian-compatible Markdown vault
│   ├── chat/                    ← canonicalized chat transcripts
│   ├── email/                   ← email bodies, signature-stripped
│   ├── doc/                     ← document chunks
│   ├── topics/                  ← per-entity topic trees
│   ├── sources/                 ← per-source summary trees
│   └── daily/                   ← global daily digest tree
└── learned_flows/
    └── *.flow.json              ← recorded multi-step UI replays (§5.2)
```

User edits `USER.md` and `HEARTBEAT.md` directly (or via Obsidian on the
vault). Background jobs (extract / admit / seal / digest) run under the
scheduler-gate policy (§5.7).

### 5.6 Cost model

Every LLM/TTS/STT/Vision call is wrapped through one accounting layer:

```rust
struct CostLedger {
    daily_spent_usd: AtomicU64,    // micro-cents
    monthly_spent_usd: AtomicU64,  // micro-cents
    daily_cap_usd: f64,
    monthly_cap_usd: f64,
    by_provider: HashMap<Provider, Spent>,
    by_kind: HashMap<CallKind, Spent>,
}

// Every provider client:
async fn call(...) -> Result<Response> {
    let est = estimate_cost(&req);
    self.ledger.reserve(est)?;        // refuses if over cap
    let resp = self.inner.call(req).await?;
    let actual = compute_cost(&resp);
    self.ledger.commit(actual);
    self.bus.publish(SpendEvent { provider, kind, usd: actual });
    Ok(resp)
}
```

UI shows live `$X.XX today / $Y.YY this month`. On cap, assistant pauses
and tells the user. Goal #12.

### 5.7 Battery model (scheduler gate, port from OpenHuman)

```rust
enum Policy {
    Aggressive,   // server/cloud — bypass throttles
    Normal,       // desktop with headroom — run as scheduled
    Throttled,    // busy or on battery — serialize + slow
    Paused,       // user opted out — defer indefinitely
}

// Signals refreshed every 30 s:
struct Signals {
    on_ac_power: bool,
    battery_pct: u8,
    cpu_recent_avg: u8,    // %, rolling 30-s
    deployment: Deployment,  // {Desktop, Mobile, Server}
}

// All background workers gate on:
async fn wait_for_capacity(&self) -> LlmPermit {
    loop {
        match self.current_policy() {
            Policy::Aggressive | Policy::Normal => return LlmPermit::new(),
            Policy::Throttled => sleep(Duration::from_secs(30)).await,
            Policy::Paused => sleep(Duration::from_secs(60)).await,
        }
    }
}
```

On Android specifically, the daemon goes Throttled when:

- `BatteryManager.isCharging() == false` and `level < 80`, or
- CPU usage averaged across the last 30 s exceeds 70 %.

It goes Paused when the user explicitly toggles it or when `PowerManager`
reports power-save mode.

### 5.8 Privacy enforcement

Three concrete mechanisms:

1. **Radioactive field redaction at the AX serializer.**
   ```kotlin
   // BenAccessibilityService.kt — *before* returning the tree
   fun AccessibilityNodeInfo.toJson(): JSONObject {
       val obj = JSONObject()
       if (isPassword || className == "android.widget.EditText" && inputType and
               InputType.TYPE_TEXT_VARIATION_PASSWORD != 0) {
           obj.put("value", "<REDACTED:password>")
       } else {
           obj.put("value", text?.toString())
       }
       // ... same for OTP-like patterns (6 digits in a field tagged
       // "code"/"otp"/"verification")
   }
   ```
   Same in `macos_accessibility.py` for fields where `AXSubrole == "AXSecureTextField"`.

2. **No screenshot persistence by default.**
   Vision-layer screenshots live in memory only. They are not written to
   disk unless `OPENHUMAN_DEBUG_DUMP_SCREENSHOTS=1`. Session JSONLs include
   only the action, not the pixel data.

3. **Per-call privacy badge.**
   Every tool call result includes `data_left_device: bool`. The session
   timeline shows a small icon for every call where this is true. User
   can audit at any time.

---

## 6. Honest assessment — drawbacks & trade-offs

Ranked by how soon and how badly each will bite.

### 6.1 Will bite hard, soon

| # | Issue | Why it's bad | Fix |
|---|---|---|---|
| 1 | **`nodejs-mobile` is abandoned** (~2022 last meaningful release). We ship Node 18 inside the APK; Node 18 reached EOL in April 2025. 85 MB of the APK is `libnode.so` per ABI. | When Android 16/17 tighten JNI rules (they will), `libnode.so` has no maintainer. Security patches stop. APK size pain forever. | Delete `nodejs-mobile` entirely. Move all JS-side logic into the Rust core via JNI. Drops APK to ~12 MB. (§7) |
| 2 | **`SpeechRecognizer` is the wrong wake-word engine.** Not designed for always-on listening, OEM-throttled, may stream to Google cloud, fuzzy-match yields false positives + missed wakes. | Battery cost is high. Goal #11 (battery parity) is unreachable. | Replace with **openWakeWord** (Apache-2.0, ONNX, on-device, <50 mW) or **Picovoice Porcupine** (free for non-commercial open-source). |
| 3 | **OpenAI Realtime API for Android voice will blow the cost cap.** `gpt-4o-realtime-preview` is ~$0.06/min in + $0.24/min out. A 5-min conversation/day ≈ $45/month per device. | Goal #12 unreachable. | Replace with Groq Whisper (STT, ~$0.04/hr) + Cartesia Sonic or Deepgram Aura (TTS) + any text LLM. Same UX, ~10× cheaper. |
| 4 | **`gpt-4o` vision fallback violates the privacy non-negotiable.** Goal #6 says "no sensitive field leaves device." Screenshots contain email subjects, sender names, message snippets, banking pages, OTPs. Today this is going to OpenAI by default. | Goal violation. Audit failure. | 4-layer cascade (§5.1): on-device VLM (Gemma 3n / Qwen2-VL / Apple Foundation Models) ahead of cloud vision. Cloud vision is logged + capped + UI-badged. |
| 5 | **OpenClaw single-vendor lock-in.** Both sides depend on `openclaw@latest`. If it deprecates a flag or stops shipping, both planes break in lockstep. | Single point of failure. | Replace with a Rust-native ReAct loop in `pa-core` (~500 lines). OpenClaw becomes optional. |

### 6.2 Will bite eventually

| # | Issue | Why it's bad | Fix |
|---|---|---|---|
| 6 | **UDP multicast wake arbiter** doesn't work on corporate WiFi, public WiFi, hotspots, eSIM-only cellular. | Wake-arbiter fails silently in exactly the situations users would notice (office, café). | Rendezvous over existing peer link (§5.4). |
| 7 | **Three languages, two peer protocol implementations** (Python on Mac, JS port on Android). | Wire compat will drift. One side adds a field, the other silently drops it. | Single Rust implementation called from both via FFI. |
| 8 | **iOS in `GOAL.md` won't work the way Mac+Android does.** iOS AX is read-only for third-party apps; you cannot synthesize taps. | If we plan as if iOS = Mac, we redesign in 6 months. | Spell it out now: iOS = read + dispatch via App Intents + Shortcuts; UI execution delegated to Mac via Continuity / iPhone Mirroring. |
| 9 | **Sideload-only distribution.** Google Play won't accept an app whose primary purpose is using AccessibilityService to drive other apps. | No auto-updates, no rollback, no telemetry, no friction-free re-install. | Firebase App Distribution / GitHub Releases + a self-update flow like OpenHuman's `update/`. |
| 10 | **Manufacturer kill behavior** (Xiaomi, OnePlus, Realme, Samsung S-series). | The "Tasker tax" — your FGS dies despite the foreground-mic permission. | Per-OEM detection + per-OEM whitelisting walkthrough in onboarding. |
| 11 | **No cost meter yet.** Goal #12 deferred. | Invisible-spend bugs accumulate. | Build now, not later. |
| 12 | **Radioactive-field redaction is a promise, not a verified code path.** I see no test that asserts a password field's value isn't returned. | One careless future change leaks an OTP. | Add explicit `password_in_tree.test.kt` + `password_in_tree.test.py`. |

### 6.3 Will bite when we scale

| # | Issue | Why it's bad | Fix |
|---|---|---|---|
| 13 | `memory.json` is going to outgrow itself. Atomic-write KV works for ~1k facts. | Once we ingest Gmail/Slack, we need SQLite + FTS5 + vectors. | Port `memory/tree/` from OpenHuman. |
| 14 | No learned-flow replay yet. | Goal #9 (sub-2-second repeat tasks) unreachable. | §5.2 — top of the 90-day plan. |
| 15 | No "active focus" lease between devices. | "Pause" while music plays on phone → both devices pause. | Active-focus lease in `peer.delegate` envelope. |

---

## 7. Target architecture (the pivot)

Big idea: **one Rust core, three thin native shells.** This is the
architectural pattern we'd lift from agent-browser + OpenHuman combined.

```
                            your repo
                            ─────────
crates/
├── pa-core/                  ← business logic, ~70% of LOC, builds for every target
│   ├── peer/                 (1 implementation; matches current proto schema)
│   ├── memory/               (port OpenHuman memory/tree/ — Obsidian vault + SQLite)
│   ├── tokenjuice/           (vendor OpenHuman tokenjuice/ — 80% cost reduction)
│   ├── scheduler_gate/       (vendor OpenHuman scheduler_gate/ — battery throttling)
│   ├── subconscious/         (port OpenHuman heartbeat/ + subconscious/)
│   ├── approval/             (S0/S1/S2/S3 + radioactive-field redaction)
│   ├── providers/            (OpenAI / Anthropic / Groq / Cartesia / local — BYOK router)
│   ├── platform_backend/     (TRAIT — see below)
│   ├── tools/impl/           (cross-platform: web_fetch, memory_*, weather, cron)
│   ├── skills/               (parse SKILL.md, inject per turn, 8 KiB cap, rust-embed)
│   ├── cost_ledger/          (per-call accounting, daily/monthly cap)
│   ├── learned_flows/        (record + replay multi-step UI sequences)
│   └── wake/                 (openWakeWord ONNX via `ort` crate, arbiter logic)
│
├── pa-mac/                   ← <2k LOC Rust, links pa-core
│   ├── ax/                   (AXUIElement via `accessibility-sys` or swift-bridge)
│   ├── service/              (launchd plist generator)
│   └── menubar/              (tiny SwiftUI menu-bar app loads .dylib)
│
├── pa-android/               ← <2k LOC Rust, builds .so via cargo-ndk
│   ├── jni/                  (Kotlin calls these)
│   ├── ax/                   (talks to Kotlin AccessibilityService via callbacks)
│   └── service/              (FGS bootstrap + battery exemptions)
│
└── pa-ios/                   ← later, mostly read-only
    ├── intents/              (App Intents via swift-bridge)
    └── shortcuts/            (Shortcuts URL scheme caller)

android/                      ← existing Kotlin tree, slim down dramatically
└── app/src/main/kotlin/com/ben/
    ├── BenForegroundService.kt       (mic FGS host)
    ├── BenAccessibilityService.kt    (AX, thin JNI relay to pa-core)
    ├── BenScreencapService.kt        (MediaProjection)
    ├── BenRustBridge.kt              (NEW: JNI to libpa_core.so — replaces ALL Node)
    └── MainActivity + onboarding/    (UI)
    NO MORE assets/node/, NO MORE libnode.so, NO MORE assets/node/src/peer/

mac/                          ← Swift menu-bar app
└── Ben.app/                  (NSStatusItem, loads libpa_core.dylib)
```

### 7.1 The `PlatformBackend` trait

Lift directly from agent-browser's `BrowserBackend`:

```rust
#[async_trait]
pub trait PlatformBackend: Send + Sync {
    // Discovery
    async fn list_apps(&self) -> Result<Vec<AppInfo>>;
    async fn focused_app(&self) -> Result<AppInfo>;
    async fn focus_app(&self, identifier: &AppIdentifier) -> Result<()>;
    async fn launch_app(&self, identifier: &AppIdentifier) -> Result<()>;

    // Tree
    async fn read_screen(&self, opts: ReadScreenOpts) -> Result<AxTree>;
    async fn screen_size(&self) -> Result<ScreenSize>;
    async fn screenshot(&self) -> Result<ScreenshotBytes>;

    // Action
    async fn click(&self, target: ClickTarget) -> Result<()>;
    async fn type_text(&self, text: &str) -> Result<()>;
    async fn scroll(&self, direction: ScrollDirection, amount: u32) -> Result<()>;
    async fn swipe(&self, from: Point, to: Point, duration_ms: u32) -> Result<()>;

    // Capability negotiation
    fn capabilities(&self) -> BackendCapabilities;
}

// Implementations:
//   MacOsBackend       → pyobjc/swift-bridge AXUIElement + CGEvent
//   AndroidBackend     → JNI ↔ BenAccessibilityService
//   IosBackend         → App Intents + Shortcuts (limited capabilities)
//   BrowserBackend     → CDP attached to user's real Chrome
//   AgentBrowserBackend → optional; wraps Vercel agent-browser sandbox
```

The agent reasons about an abstract surface. The backend handles platform
calls. The same LLM prompt drives Outlook on Mac and WhatsApp on Android
because the tool surface is identical.

### 7.2 Why this fixes "Mac works, Android doesn't"

Today: Python peer ↔ JS peer ↔ Kotlin JSON-RPC = three seams. Most of
the Android breakage is at the JNI/Node/Kotlin/AccessibilityService
boundaries.

After pivot: One Rust crate, one JNI seam (~200 lines), well-tested
patterns (`cargo-ndk` + `androidx.test`). The Android shell becomes
mostly UI + AccessibilityService + a thin JNI bridge.

### 7.3 What we delete

- `omniclaw/peer/` (Python) — replaced by `crates/pa-core/peer/`.
- `assets/node/` (Android embedded JS, ~85 MB worth).
- `libnode/bin/`, `app/src/main/cpp/native-lib.cpp`, CMake hosting Node.
- `omniclaw/voice/wakeword_mac.py`, `omniclaw/wake/arbiter.py`,
  `omniclaw/peer/*.py`.
- The OpenClaw npm dependency on both sides (it becomes optional).

### 7.4 What we keep

- All `.kt` UI / Onboarding / Pairing / Settings / History / MicTest.
  That's UX, leave it.
- `omniclaw/tools/macos_*.py` — keep as a Python CLI fallback during
  migration; long-term port to native Rust AX via `accessibility-sys`.
- `jarvis://pair` deep link + QR + EncryptedSharedPreferences.
- `SKILL.md` convention, `USER.md`, `AGENTS.md`, `TOOLS.md`, `SOUL.md`.
- Sensitivity classification, tool registry shape, peer protocol schema.

---

## 8. Roadmap — next 90 days

### Weeks 1–2 — Stop the bleeding (do these regardless of any rewrite)

| Item | Why now | Effort |
|---|---|---|
| Live cost meter (`cost_ledger/`) | Goal #12. Need a real number per call before we change providers. | 3 days |
| Radioactive-field redaction in AX serializer (Mac + Android) + tests | Privacy non-negotiable + audit hygiene. | 2 days |
| Replace SpeechRecognizer wake-word with **openWakeWord** ONNX | Battery + privacy. | 4 days |
| Replace OpenAI Realtime for Android voice with **Groq Whisper + Cartesia / Deepgram** | Cost cap. ~10× cheaper, no UX regression. | 1 week |
| **Learned-flow replay v1**: record + replay for any task that succeeded once | Goal #9. Single biggest UX/cost lever. | 1 week |

### Weeks 3–6 — Stand up `pa-core`

- New Rust workspace, `pa-core` crate.
- Vendor / port: `memory/tree/`, `tokenjuice/`, `scheduler_gate/`,
  `subconscious/` from OpenHuman (license-check first — OpenHuman is GNU,
  verify compatibility).
- Port `omniclaw/proto/types.rs` to `pa-core/peer/`. One implementation
  for both sides.
- `pa-core` builds for `aarch64-apple-darwin`, `aarch64-linux-android`,
  `x86_64-apple-darwin`, `x86_64-pc-windows-msvc`.

### Weeks 7–10 — Migrate Android off `nodejs-mobile`

- Wire `pa-core` into Android via `cargo-ndk`. JNI bridge to existing
  Kotlin services.
- Delete `assets/node/`, `libnode/bin/`, `src/main/cpp/`. APK shrinks
  from ~85 MB to ~12 MB.
- The Android peer client is now `pa-core`'s. One language, one
  protocol, one wire schema, one bug surface.

### Weeks 11–13 — Migrate Mac to thin shell

- Tiny SwiftUI menu-bar app + `libpa_core.dylib`.
- Keep `omniclaw/tools/macos_ax.py` as `pa-core/tools/impl/macos_ax_python.rs`
  fallback for one release.
- Replace `ai.jarvis.peer.plist` sed-templating with
  `pa-core/service/macos.rs` plist generator (lifted from OpenHuman).

### Week 14 — Document the new architecture

- Mark `Old_architecture_no_use_now.md` truly old.
- Write `NEW_ARCHITECTURE.md` (the post-pivot version of this document).
- Update `GOAL.md` §"How it must behave" to reflect iOS = read + dispatch.
- Update `BEN_ANDROID_SETUP.md` for the new APK shape.

### What lands in week 14

A daily-driver Jarvis where:

1. Wake word fires on-device, only one device responds.
2. Voice round-trips in <1.5 s for repeat tasks (cached-flow replay), <4 s
   for novel tasks.
3. APK is ~12 MB instead of 85 MB.
4. Memory tree ingests Gmail / Slack / WhatsApp on a 20-min loop.
5. Standing-order tasks (morning brief, evening wrap, bill watcher,
   birthday reminders) run from `HEARTBEAT.md`.
6. Sub-$10/month total LLM + voice + vision cost for typical use.
7. Battery delta on Android is <5 % over a baseline day (measured, not
   assumed).
8. Cross-device tasks work on LAN + over Tailscale.
9. iOS plan is documented as "read + dispatch via Continuity," not "the
   third Mac+Android equivalent."

---

## 9. Appendix

### 9.1 Reference repos analyzed

| Repo | URL | Why we studied it | What we took |
|---|---|---|---|
| `HKUDS/AI-Trader` | https://github.com/HKUDS/AI-Trader | Agent-native API + skill-file pattern | SKILL.md convention, worker-vs-RPC separation, tiered retention |
| `vercel-labs/agent-browser` | https://github.com/vercel-labs/agent-browser | Rust daemon + CDP + accessibility refs | `PlatformBackend` trait pattern, `@eN` refs, codegen from protocol JSON, embedded skills via `rust-embed`, persistent-daemon-thin-client model |
| `tinyhumansai/openhuman` | https://github.com/tinyhumansai/openhuman | Closest existing equivalent of the Mac side | `memory/tree/`, `tokenjuice/`, `scheduler_gate/`, `subconscious/`, `service/` installers, `providers/router.rs`, Sentry secret scrubbing |

### 9.2 Key external dependencies we plan to adopt

| Purpose | Library | License | Why |
|---|---|---|---|
| Wake word | **openWakeWord** | Apache-2.0 | On-device, free, ~50 mW, ONNX, no cloud |
| STT (post-wake) | **Groq Whisper-large-v3** | Apache-2.0 server, BYOK | ~$0.04/hr, 200 ms latency |
| TTS | **Cartesia Sonic** or **Deepgram Aura** | Commercial BYOK | Cheap, low-latency, BYOK |
| On-device VLM (Mac) | **Qwen2-VL-2B** via `mlx-vlm` | Apache-2.0 | Free, ~300 ms on M2+, no leak |
| On-device VLM (Android) | **Gemma 3n** via MediaPipe LLM | Gemma TOU | Free, ~1 s on recent flagships, no leak |
| On-device VLM (iOS 18+) | **Apple Foundation Models** | Apple SDK | Free, no key, on-device |
| Vector + FTS | **rusqlite** (bundled SQLite + FTS5) + **`hnsw_rs`** | MIT | Local-only, no extra service |
| Peer transport | **tokio-tungstenite** + **rustls** | MIT / Apache-2.0 | Async WSS, cross-platform |
| Off-LAN tunnel | **Tailscale** | Free for personal | No port forwarding |
| Mac AX | **`accessibility-sys`** (Rust FFI to AXUIElement) | MIT | Avoids Python deps |
| Android NDK | **`cargo-ndk`** + **`jni-rs`** | MIT / Apache-2.0 | Standard Rust-on-Android |
| Telemetry | **`sentry`** (Rust SDK) with `before_send` redaction | BSD-3 | Crash reports without leaking |

### 9.3 Industry parallels & academic references

Worth knowing when explaining the design to others:

- **OpenAI Operator** (Jan 2025) — cloud agent that controls a sandboxed
  browser. Vision-primary. We are AX-primary; opposite trade-off.
- **Anthropic Computer Use** (Oct 2024) — Claude takes screenshots and
  emits clicks. Vision-primary. Same trade-off as Operator.
- **Apple Ferret-UI** (Apr 2024) — academic paper on UI-tuned vision-language
  models. Inspiration for our on-device VLM layer.
- **Google Project Mariner** (Dec 2024) — browser agent. Vision-primary,
  Chrome-only.
- **Microsoft OmniParser** (Oct 2024) — converts UI screenshots to structured
  schema. Useful pre-processing for our cloud-vision layer.
- **AppAgent (Tencent), Mobile-Agent (Alibaba MARS), AutoDroid (THU),
  DroidBot-GPT, DigiRL** — academic Android-AX agents. Same surface as
  our Android side; none ship a personal-assistant APK. Cite as prior art.
- **Rabbit R1 / Humane Pin** — failed hardware that attempted a slice of
  this. The bar to clear.

### 9.4 Glossary

| Term | Definition |
|---|---|
| **AX** | Accessibility — the OS-level API exposing structured UI metadata (roles, labels, bounds, actions) for screen readers. Both macOS (AXUIElement) and Android (AccessibilityNodeInfo) expose this. |
| **AX-id** | A stable, deterministic identifier for a node in the AX tree, used by the agent to click reliably across sessions. |
| **`@eN`** | Token-efficient element ref convention borrowed from agent-browser. `@e7` = the 7th interactive element in the current AX dump. |
| **BYOK** | Bring-your-own-key. User provides their own LLM API key; no subscription. |
| **Click cascade** | Our 4-layer (5 with cloud fallback) policy for resolving "where to click": AX-id → AX-label → on-device OCR → on-device VLM → cloud VLM. |
| **CDP** | Chrome DevTools Protocol. WebSocket JSON-RPC for controlling Chrome. |
| **CGEvent** | macOS event-tap API for synthesizing mouse + keyboard events. |
| **Composio** | Backend-as-a-service that exposes 1000+ OAuth integrations through one API. OpenHuman uses it; we may add it as opportunistic alongside AX. |
| **FGS** | Foreground Service (Android). A service that runs with a persistent notification and isn't killed by the OS as aggressively. |
| **HMAC** | Hash-based Message Authentication Code. We use HMAC-SHA256 over JSON-RPC messages with a shared pairing secret. |
| **Learned flow** | A recorded multi-step UI sequence that can be replayed without LLM calls. Our cost/latency optimization. |
| **OpenClaw** | The npm/CLI agent framework the project currently uses as its brain. To be replaced by a Rust-native ReAct loop in `pa-core`. |
| **Peer mesh** | Two (eventually three) device-agents that find each other and coordinate, with no central server. |
| **Radioactive field** | An AX node whose value must never be read into memory, logged, or sent to any model. Passwords, OTPs, card numbers. |
| **ReAct** | Reason+Act — the agent loop pattern where the LLM alternates between thinking ("I should open Outlook") and tool calls ("device.launch_app('Outlook')"). |
| **Scheduler gate** | OpenHuman's pattern for battery-aware throttling of background AI work. Adopted in `pa-core`. |
| **Sensitivity (S0–S3)** | Our classification of every action by how cautiously to execute: S0 (just do it) → S3 (never auto-execute, hand back to user). |
| **Subconscious** | The "always thinking" loop that runs between user turns, reading `HEARTBEAT.md` and executing standing-order tasks. |
| **Tailscale** | The mesh VPN we use for off-LAN peer transport. |
| **Tokenjuice** | OpenHuman's tool-output compaction layer (port of `vincentkoc/tokenjuice`). ~80% token reduction on tool spam. |
| **VLM** | Vision-Language Model. We use small on-device VLMs (Qwen2-VL-2B, Gemma 3n) for the 4th layer of the click cascade. |

---

*Last updated: 2026-05-12. Maintainer: @samarthyadannavar.*
