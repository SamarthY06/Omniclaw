# USER.md — About the user

> Edit this file with your details. The agent reads it at session start.
> All fields below are templates — replace the placeholders with real values.
> Empty entries are fine.

## Identity

- **Name:** _Samarth_
- **Preferred name:** _Sam_
- **Pronouns:** _he/him_
- **Timezone:** _Asia/Kolkata (IST, UTC+5:30)_
- **Working hours:** _Mon-Fri 09:00-19:00; flex on weekends_

## Devices

- **Mac:** primary work machine. Has Accessibility permission for the
  agent. Standard installed apps include browsers, Notes, Calendar, Mail,
  Slack, VS Code, Cursor, Terminal, Messages, FaceTime.
- **Phone:** paired Android. Has the Jarvis APK with AccessibilityService
  permission and battery whitelisted. Standard installed apps include
  WhatsApp, Telegram, Gmail, Google Calendar, Maps, Spotify, Swiggy,
  Zomato, Uber, paytm, gpay.

## Important contacts

> The agent will read this for "send to <name>" requests.

- _Dad: WhatsApp +91-XXXXXXXXXX_
- _Mom: WhatsApp +91-XXXXXXXXXX_
- _Manager: <name>, Slack, email_
- _Best friend: <name>, WhatsApp +91-XXXXXXXXXX_

## Preferences

- **Voice:** alloy (default Realtime voice). Switch to verse if I ask.
- **Default reply length:** terse. One sentence unless a deep dive is asked.
- **Confirmation policy:**
  - Auto-execute: read-only, navigation, screenshots, screen reads.
  - Confirm: anything that sends a message, makes a payment, installs
    or removes software, changes system settings.
  - Hand off (S3): OTPs, biometric, password entry, first-time recipient
    payments — let me do it on the device.
- **Cost cap:** ~$10 per month for LLM calls. Pause and tell me if we
  approach that.
- **Privacy:** Do not log full prompts to disk. Summaries only.

## Standing orders

> See AGENTS.md for the implementation. These are the schedules I want.

- 07:00 weekdays — Morning brief.
- 09:00 daily — Birthday reminders for the next 7 days.
- 21:00 daily — Evening wrap (today's commitments closed, tomorrow's preview).
- 15 min before any calendar event with attendees — Meeting prep.
- Hourly — Bill watcher (scan inbox for due-this-week bills).

## Things I tend to ask for

> Hints; not exhaustive.

- "Order biryani" -> Swiggy, my usual place (set on phone).
- "Open Cursor" / "Cursor pls" -> launch Cursor.app, focus the most recent project.
- "Read me my unread WhatsApps" -> peer task.run; only unread; skip groups.
- "Plan my week" -> spawn subagents per project, draft a doc, ask before saving.

## Things I never want

- "Reply" or "Send" without a confirmation step on first-time recipients.
- Calendar invites changed without telling me.
- Apps installed without asking.
- Messages on my behalf to my manager.
