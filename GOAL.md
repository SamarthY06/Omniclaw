# Goal — A Jarvis For My Devices

This document is the problem statement and the vision. It does not name any specific framework, library, app, model, or service — those decisions belong in the build plan. This document only describes **what we are trying to build, and why**, so that whatever we build can be measured against it.

The companion file `USE_CASES.md` lists the concrete tasks the assistant must handle. This file describes what the system around those use cases must look like.

---

## What I am trying to build

I am building a **personal assistant that behaves like Jarvis** — one assistant that I talk to in plain language, that controls any of my devices (Mac, Android, iOS) to actually get things done for me. Not a chatbot. Not a smart speaker. A real assistant that opens apps, taps buttons, types text, sends messages, places orders, runs scripts, sets alarms, makes calls, and coordinates work across whichever of my devices is the right one for the job.

It is not Siri. It is not Google Assistant. It is not Alexa. Those have a fixed, narrow command vocabulary, are vendor-locked, depend on the cloud for everything, and break the moment the task gets specific or multi-step. They cannot order biryani from Swiggy. They cannot reply to a particular WhatsApp message with the right tone. They cannot find a file on my Mac and send it to dad on my phone. They cannot run a Python script on my Mac and read me the result. They are voice command shells, not assistants.

What I want covers **everything in `USE_CASES.md`** — every category, every example, in natural conversational language, on whichever device I'm holding or near.

---

## What it concretely is

Concretely, the system is a set of **apps**, one installed on each of my devices, that:

1. **Run 24/7 in the background** on every device they're installed on. They start when the device boots. They stay alive when the screen is off. They survive restarts. I never have to "open" the assistant — it is always already there.

2. **Listen for a wake word.** I say the wake word ("Hey Ben" or whatever I configure), and the device starts paying attention. After the wake word I just speak normally — "order biryani from Swiggy" or "send me the offer letter on my Mac" — and the assistant goes and does it.

3. **Have their own agent locally on the device.** Each device's app is a complete agent. The phone has its own brain. The Mac has its own brain. Neither is a passive remote control for the other. Neither needs to ask the other for permission.

4. **Talk to each other when they need to.** When a task spans devices, the device that heard me coordinates with the other device's agent. There is no central hub or master. They are peers.

5. **Use my own LLM API key.** No subscriptions to anything. I plug in my OpenAI or Claude key and the assistant runs.

That is the form factor. Apps on devices. Always running. Wake word activated. Each one autonomous. Peer-to-peer when needed. BYOK.

---

## How it must behave — the picture in detail

### One assistant, one wake word, multiple devices

I should be able to install the app on my Mac, my Android phone, my iPhone (later), and from that point on I have **one assistant** that lives across all three. Same name. Same memory. Same wake word.

When I say the wake word and only one device is in earshot, that device responds. When more than one device hears me at the same time (laptop and phone both on my desk), **only one responds** — whichever is closer or whichever is the better fit for what I'm about to say. Not both. Not stacked. Not echoing. One.

### Each device does its own work — no unnecessary round-trips

This is the part that current solutions get wrong, and the part I care about most:

> If I say "order biryani from Swiggy" **on my phone**, the phone agent must do it itself. It must open Swiggy on the phone, navigate the menu, find the restaurant from where I ordered last Friday (looked up from local memory), add the biryani to the cart, go to checkout, stop at the payment screen, and hand off to me to pay. **The Mac is not involved. The Mac does not need to be on. The Mac does not need to be reachable. The phone does not need WiFi to my home network. The phone is a complete agent on its own.**

The same applies in reverse. If I'm on my Mac and I say "open my IDE in the OmniClaw project and run the test suite," the Mac agent does it. The phone is not involved.

The phone should be able to handle every use case in `USE_CASES.md` that is naturally a phone task — calls, SMS, alarms, food delivery, cab booking, photos, native-app navigation, Spotify, WhatsApp, gallery, contacts, settings, app installation — entirely on its own.

The Mac should be able to handle every use case that is naturally a Mac task — filesystem, IDE, large-screen browsing, file operations, code execution, Notes, Mail, Calendar, system settings, full Playwright-driven web tasks — entirely on its own.

### Devices coordinate when (and only when) they need to

Some tasks genuinely cross devices. Those must work too:

> "Hey Ben, send me the offer letter PDF that's on my Mac to me on WhatsApp." Said from my phone. The phone agent recognizes this needs the Mac. It securely reaches my Mac agent (over local network at home, or over a secure tunnel when I'm out), asks the Mac to find and stage the file, transfers the file back, opens WhatsApp on the phone, and sends it. One sentence from me, two devices coordinating, done.

> "Hey Ben, run the deploy.py script in my OmniClaw repo on my Mac and read me the result." Said from my phone. The phone agent reaches the Mac, the Mac runs the script, the Mac sends back the output, the phone reads it to me aloud.

> "Hey Ben, on my phone, take a photo with the front camera right now." Said from my Mac. The Mac agent reaches the phone, the phone captures, sends back the photo.

The handoff is invisible to me. I don't say "phone, ask Mac to..." — I just say what I want, and the device that heard me figures out who needs to do what.

### Sensitive things stop and ask

For payments, OTPs, passwords, PINs, card numbers, biometrics, government IDs — the assistant **always pauses and hands the screen back to me**. It navigates to the right screen, shows me what's about to happen, and waits. I enter the OTP / approve the payment / type the password myself. The assistant can monitor passively to detect when I'm done, then resume the flow on the other side.

The assistant **never** types my passwords. **Never** enters my OTP. **Never** completes a payment on my behalf. This is a hard line, not a soft preference.

For other "important" actions (sending a message, placing an order, deleting something, making a call to someone I don't message often), the assistant confirms with me first in plain language, then proceeds.

For everything else (reading, navigating, scrolling, searching, setting an alarm, opening an app, replying to someone I message all day) — it just does it.

### It runs always, even when I'm not using it

Beyond what I explicitly ask it to do, the assistant takes initiative on a schedule, the way a real human assistant would:

- Morning briefing at 7 AM: overnight emails I care about, today's calendar, weather, anything urgent.
- 15 minutes before each meeting: who's on the call, what the agenda is, recent emails with the attendees, the link.
- Evening wrap at 9 PM: what I did today, what's pending tomorrow, alarms set for the morning.
- Bill watcher: detects bill emails, adds to calendar, reminds me 2 days before.
- Birthday reminders: 7 days and 1 day out.
- Email triage: auto-archive promo, label by topic, draft replies for routine messages.
- Failed-task retry: re-attempts errored cron jobs, escalates to me after 2 retries.

These run because the app is always alive on the device, not because I prompted it.

### It works when I'm out

When I'm at home connected to home WiFi, my Mac and phone agents talk to each other directly over the local network. Fast. No internet round-trip needed.

When I leave home — at office, in a cafe, traveling — the phone keeps doing every phone-local thing without missing a beat. And when I do need something Mac-side from out (a file, a script, a browser session), the phone reaches my Mac securely **from anywhere** without me having to set up port forwarding, expose anything publicly, or fiddle with networking. The two agents find each other, authenticate, do the work, and disconnect.

If neither LLM nor network is available, the assistant tells me clearly, and queues the task for when connectivity returns.

### It learns the things it needs to learn

The assistant remembers what it has to remember to make the use cases work:

- Contacts I refer to by relationship — "mom", "dad", "partner", "John"
- My home address, office address, frequent destinations
- The apps I use for what — Spotify for music, Swiggy for food, Uber for cabs, Gmail for email
- Recent task history so "again" / "like last time" / "the usual" works
- Explicit preferences I state — "always book aisle seats", "stop reading me promo emails", "default to Truffles for biryani"
- Learned multi-step UI sequences for repeated tasks, so the second time I order biryani it doesn't need the LLM at every step — the assistant replays the saved flow at full speed and almost zero cost

I can inspect, edit, and wipe everything it remembers, anytime.

---

## What success looks like — concrete scenarios

These are the kinds of moments I expect to actually happen with this system:

**Morning, in bed:** I say "Hey Ben, what's my day looking like?" The phone — which is on my nightstand, charging — speaks back briefly: my 10 AM call with John is the only meeting, an Amazon package is out for delivery, weather is 28°C clear, the offer letter from Acme arrived overnight.

**At my desk on the Mac:** I say "Hey Ben, send the offer letter on my desktop to dad on WhatsApp, and after that call mom on my phone." The Mac finds the file. The phone receives it, opens WhatsApp, attaches it, sends it. Then the phone places the call to mom. Both happen while I keep working. I never picked up the phone.

**Leaving for office:** I say "Hey Ben, queue up my drive playlist on the phone, set DND on both devices till 6 PM, and remind me to call dad when I leave the office." The Mac goes silent. The phone starts the playlist. DND is on. A location-based reminder is set. I walk out.

**At lunch on phone:** I say "Hey Ben, biryani like last Friday." The phone opens Swiggy, finds the restaurant from memory, adds the right item, navigates to checkout, stops at payment. I authorize. Order placed. ETA delivered to me.

**In a cab, away from home WiFi:** I say "Hey Ben, in the deploy script on my Mac, change the region to ap-south-1 and run it. Tell me when it's done." The phone reaches my Mac over a secure tunnel. The Mac edits the file, runs the script, sends back the result. The phone reads it aloud.

**Evening:** "Hey Ben, wrap up." The phone summarizes my day — what got done, what's pending, what's tomorrow. Alarms are set. Tomorrow's morning briefing is queued.

That is the bar. That is the day-to-day target. Anything that can't deliver this is not a personal assistant.

---

## Operational properties (non-negotiable)

These are the properties the system must have, regardless of which framework or implementation we choose:

### Always-on, background, autonomous
- Apps run as long-lived background services on each device, started at boot, surviving screen-off and battery-saver modes.
- They are awake to wake-word and to scheduled triggers without me opening them.

### Voice-first, hands-free
- Wake word triggers the device; only one device responds when more than one hears.
- Natural conversational speech in, brief useful speech out.
- I can interrupt at any time ("stop", "cancel", "actually do X instead").

### Per-device autonomy with peer coordination
- Each device's agent is complete on its own. Phone tasks happen on the phone. Mac tasks happen on the Mac.
- Cross-device coordination is explicit, secure, and only invoked when the task genuinely spans devices.
- No central master, no required hub.

### Bring-your-own LLM key
- I plug in my own OpenAI / Claude / other key. No required subscriptions. No SaaS middleman.
- I can switch providers without rewriting anything I've configured.

### Safety by classification
- Every action is classified by sensitivity (safe / reversible / important / sensitive).
- Sensitive actions (passwords, OTPs, PINs, payments, biometrics, government IDs, account deletion) are never auto-executed — the assistant hands the screen to me.
- Important actions (send, order, delete) confirm before executing.
- Safe and reversible actions just happen.

### Privacy
- Everything that can run on-device runs on-device: UI reading, action execution, local memory, scheduling.
- API keys live in OS keychains (macOS Keychain, Android Keystore). Never in plaintext config or repository code.
- Sensitive UI fields are radioactive — never read into memory, never logged, never sent to any model.
- All my data is local, exportable, and wipeable in one command.

### Cost-aware
- Repeated tasks become near-free over time via learned-sequence caching — the LLM is called for the first execution, not the tenth.
- Pre-task estimates for expensive tasks. Daily and monthly caps that pause the assistant when hit.
- I can see, anytime, exactly what I've spent.

### Battery-aware on the phone
- No continuous cloud audio streaming for wake-word detection — wake word runs on-device.
- No high-frequency background polling of the screen.
- Cached UI flows replay device-side without LLM calls.
- Heavy work (long observation, large file processing) prefers the Mac when both devices are reachable.

### Reliable
- Tells me plainly when it can't do something. Doesn't fabricate progress. Doesn't pretend.
- Retries reasonable failures, escalates clearly when it gives up.
- Resumes from the last completed step on crash, not from scratch.

### Maintainable by me, in plain text
- Adding a contact, a routine, a new app the assistant should know about, or a new restriction is editing a few lines of human-readable text — not writing code.
- Changing the assistant's name, wake word, voice, or tone is a single setting.
- One-command backup and restore of everything (memory, preferences, learned flows).

---

## Definition of done

The system meets this goal when, on a normal day:

1. I can install the app on my Mac and on my Android phone, configure my LLM API key once, and from then on never have to "launch" the assistant — it is always running on both.
2. A single wake word reliably triggers exactly one device — the right one — even when both are in earshot.
3. Every category of task in `USE_CASES.md` works, by voice, on the right device, end-to-end.
4. A phone task said from the phone (e.g., "order biryani from Swiggy") executes entirely on the phone, with the Mac off or unreachable.
5. A Mac task said from the Mac executes entirely on the Mac, with the phone off or unreachable.
6. A cross-device task said from either side works without me scripting the handoff — including when I am away from home WiFi.
7. Sensitive actions (payments, OTPs, passwords) are never auto-executed; the assistant always navigates to the screen and hands it back to me.
8. Important actions confirm with me first; safe and reversible actions just happen.
9. Repeated tasks complete in under 2 seconds with negligible LLM cost (learned sequences replay).
10. Standing-order tasks (morning brief, meeting prep, evening wrap, bill/birthday reminders) run on schedule without me triggering them.
11. The phone's battery life is not measurably worse with the assistant installed than without.
12. My monthly LLM cost is bounded and visible to me.
13. None of my data leaves the devices except calls to the LLM provider I chose, and no sensitive field ever does.

If all thirteen are true, the assistant has cleared the bar.

---

## Why current options don't solve this

To be explicit about why I'm not just using something off the shelf:

- **Siri / Google Assistant / Alexa** — narrow command vocabulary, vendor-locked, cloud-required, can't navigate arbitrary apps, can't compose contextual messages, can't coordinate across devices, can't be customized with my own preferences or my own LLM key.
- **ChatGPT / Claude apps** — chatbots, not agents. They can't actually open Swiggy and place an order. They can't drive UI. They have no concept of which of my devices is the right one for a task. They don't run in the background. They don't have wake-word activation. They don't coordinate peer-to-peer.
- **Existing agent frameworks** — most are either Mac-only with the phone as a passive remote node, or general-purpose libraries that need a lot of glue to become an actual personal assistant. None solves the "app on every device, autonomous per-device, peer-coordinated, wake-word triggered, BYOK, all use cases" combination out of the box.

The assistant I want sits in the gap none of those fill.

---

## Non-goals

To keep scope honest:

- **Not a replacement for the apps I use.** Spotify, Swiggy, WhatsApp, Gmail still own their domain. The assistant operates them on my behalf.
- **Not a coding tool.** Specialized tools exist for that. This is a general-purpose personal assistant for everyday life.
- **Not a multi-tenant SaaS product.** This is a personal tool for me. If it ever becomes a product, that is a separate decision and a separate build.
- **Not a deep AI companion.** It does not need to model my personality, predict my emotions, or hold long psychological context. Basic memory of contacts, preferences, recent task history, and learned flows is enough.
- **Not a research project.** Reliability, safety, and cost matter more than novel capabilities. It is a tool I expect to use every day.
