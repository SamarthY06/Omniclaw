# TOOLS.md - Ben on Android

This file routes the agent to the right tool. Run `--json-tools` against any
JS tool to get the OpenAI function-tool schema for live discovery.

## Tools

| Tool                     | Subcommands                                       |
|--------------------------|---------------------------------------------------|
| `node tools/android_ax.js` | `tree`, `click`, `click-at`, `type`, `swipe`, `scroll`, `focus`, `launch`, `screen-size`, `screenshot` |
| `node tools/android_vision.js` | `text-locate`, `locate`, `read`               |
| `node tools/peer_cli.js`   | `status`, `verify`, `caps`, `tools.invoke`, `task.run` |
| `node tools/history_cli.js` | `list`, `show`, `search`                         |

Discovery cadence: at agent boot, run `--json-tools` for each of the four to
hydrate the function-call menu. Cache the result for the session.

## Decision rules

### Reading text on screen
1. `android_ax.js screenshot` to get a fresh PNG.
2. `android_vision.js text-locate` first.
3. If `text_locate` score < 0.55 OR target isn't textual, escalate to
   `android_vision.js locate` (OpenAI computer tool, gpt-5.5).
4. If neither lands a coordinate, ask the user.

### Clicking elements
1. Locate first (rule above), then `android_ax.js click-at --x --y --app <pkg>`.
2. After click, screenshot again and verify the expected post-click text is
   present via another `text-locate`. If verification fails, re-locate before
   retrying.
3. **Never fall back to a browser.** If the native app can't do the task, say
   so and stop.

### Typing
1. Locate the input field (text-locate or visual locate).
2. `android_ax.js click-at` on the field.
3. `android_ax.js type --text "..."`.
4. Screenshot + `text-locate` to confirm.

### Cross-device
- Anything that's clearly a Mac task: `peer_cli.js task.run <intent>`.
- Mac responds with streamed `task.event`s; speak/log those, then wait for the
  final result.

## Tool execution shim

`exec_pattern` for OpenClaw skill files:
- `android_ax`: `node /data/user/0/com.ben/files/openclaw/tools/android_ax.js *`
- `android_vision`: `node /data/user/0/com.ben/files/openclaw/tools/android_vision.js *`
- `peer_cli`: `node /data/user/0/com.ben/files/openclaw/tools/peer_cli.js *`
- `history`: `node /data/user/0/com.ben/files/openclaw/tools/history_cli.js *`
