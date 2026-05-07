# Demo prompts

Everything below is something you can say after wake-word and Talk-mode
opens. The tool calls shown in `→` are what the agent should pick;
they're not what you have to type.

## Mac-only (no phone needed yet)

> "What's on my screen?"
> → `macos_ax.py focused-app` then `macos_ax.py tree --flat`

> "Open Cursor and create a new file in the current project."
> → `macos_ax.py launch Cursor` → `macos_ax.py shortcut cmd+n`

> "Type 'hello world' and save."
> → `macos_ax.py type "hello world"` → `macos_ax.py shortcut cmd+s`

> "Take a screenshot of Safari and save it to my desktop."
> → `macos_ax.py screenshot --app Safari`

> "What apps are running?"
> → `macos_ax.py list-apps --category running`

## Webview / Electron apps (Teams, Slack desktop, Discord)

Their AX trees come back with `(no label)` rows for the conversation pane.
The agent automatically falls back to screenshot + vision.

> "What's my latest message on Teams?"
> → `macos_ax.py focus "Microsoft Teams"`
> → `macos_ax.py tree --flat` -- sidebar gives the chat-list previews
>    directly (the `AXStaticText` lines like
>    `Tanisha: Have begun on Manitoba so that we do not cross paths`).
> → that's the answer; no vision needed for this question.

> "Read the last 5 messages from Pizza Pizza General."
> → `macos_ax.py focus "Microsoft Teams"`
> → `macos_ax.py tree --flat` → identify the Pizza Pizza row index
> → `macos_ax.py click --index N`
> → `macos_ax.py screenshot --app "Microsoft Teams"`
> → `macos_vision.py read --image <path> --question "List the last 5 messages as JSON {sender,time,text}. Return only JSON."`
> → parse, speak.

> "Is the warning banner still red?"
> → `macos_ax.py screenshot --app <app>`
> → `macos_vision.py read --image <path> --question "Is the warning banner red? Answer yes/no with one sentence."`

The vision call is **S2** — the screenshot leaves the device. In Talk mode
the policy auto-approves it (you're actively asking) and logs the call. In
headless / cron mode you'll be prompted first.

## Cross-device (phone paired)

> "Ping my phone."
> → `peer_cli.py ping`

> "What can my phone do?"
> → `peer_cli.py caps`

> "Take a photo with my phone, back camera."
> → `peer_cli.py tools.invoke take_photo --args '{"camera":"back"}'`

> "Send the file 'design.pdf' from my desktop to dad on WhatsApp."
> → exec approval will fire (it's S2)
> → on yes: `peer_cli.py task.run send_doc_via_whatsapp ...`

> "Order biryani from my usual place."
> → exec approval (S2)
> → on yes: `peer_cli.py task.run order_food --args '{"item":"biryani","preset":"usual"}'`
> → at the payment screen, the phone hands off to the user

## Standing orders (not voice — registered once, fire on schedule)

These live as English instructions in `~/.openclaw/workspace/AGENTS.md`.
The agent registers OpenClaw cron jobs from them; you don't write code:

- 07:00 weekdays — Morning brief.
- 09:00 daily — Birthday reminders.
- 21:00 daily — Evening wrap.
- 15 min before each meeting — Meeting prep.

To add a new one, just add it to AGENTS.md and tell the agent "register
my new standing orders". It will introspect the file and create the
cron jobs.

## What it should refuse / confirm

- "Send my manager a message saying I'm taking the day off." — confirms
  first.
- "Pay my Swiggy order." — refuses to auto-confirm payments; hands off
  to the phone.
- "Delete my photos." — confirms (S2).
- "Read out my OTP." — refuses (S3, hand-off only).

## Diagnostic prompts

> "Is my phone reachable?"
> → `peer_cli.py verify` — daemon checks + peer ping

> "What's my LLM bill so far?"
> → reads OpenClaw's session log + telemetry; the cap is in USER.md

> "Stop talking."
> → barge-in stops the current TTS via OpenClaw Talk's `interruptOnSpeech`

## Things that won't work (yet)

- Phone-side voice (the Android APK is the next session's build).
- Wake word on the phone.
- Out-of-LAN cross-device until both devices are on Tailscale.
