# OmniClaw

An Accessibility plugin for OpenClaw that gives the AI agent the power to control any native macOS application -- clicking buttons, typing text, scrolling, reading UI elements, and navigating any app on your Mac.

## Architecture

```
┌────────────────────────────────────────┐
│  OpenClaw Gateway (localhost:18789)    │
│  ┌──────────┐  ┌──────────────────┐   │
│  │ ReAct     │  │ Browser Tool    │   │
│  │ Agent     │  │ (Playwright)    │   │
│  │ GPT-5.4   │  │                 │   │
│  └─────┬─────┘  └────────────────┘   │
│        │                              │
│  ┌─────▼───────────────────────────┐  │
│  │ Exec Tool                       │  │
│  │ python3 macos_ax.py <command>   │  │
│  └─────┬───────────────────────────┘  │
└────────┼──────────────────────────────┘
         │
┌────────▼──────────────────────────────┐
│  macos_accessibility.py (pyobjc)      │
│  AXUIElement API — indexed elements   │
│  coordinate-based clicking            │
│  screenshot fallback for vision       │
└───────────────────────────────────────┘
```

**Three-layer tool routing:**
1. **Native apps** (Notes, Finder, Calendar, Teams, Slack) -> `exec python3 macos_ax.py` via AX APIs
2. **Websites** (Amazon, Gmail, LinkedIn) -> OpenClaw's built-in browser tool (Playwright/CDP)
3. **Fallback** -> `web_fetch` / `web_search` for read-only info

## Prerequisites

- **macOS 14+** (Sonoma or later)
- **Node.js 22+** (`brew install node` or via nvm)
- **Python 3.12+** (via miniconda or Homebrew)
- **OpenAI API key** (GPT-5.4 family)

## Installation

### 1. Install OpenClaw

```bash
npm install -g openclaw@latest
openclaw onboard    # Select OpenAI, enter your API key
openclaw doctor     # Verify everything is green
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Grant Accessibility Permission

System Settings -> Privacy & Security -> Accessibility -> Add Terminal (or your terminal app).

### 4. Verify the CLI wrapper

```bash
python3 tools/macos_ax.py screen-size
# Expected: {"ok": true, "width": 1728, "height": 1117}

python3 tools/macos_ax.py focused-app
# Expected: {"ok": true, "app": "Terminal"}

python3 tools/macos_ax.py tree --flat
# Expected: indexed list of UI elements in the focused app
```

### 5. Copy skill to OpenClaw workspace

```bash
cp -r skills/macos-accessibility ~/.openclaw/workspace/skills/
```

### 6. Start the Gateway

```bash
openclaw gateway start
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `launch "App"` | Open app, wait until ready, return indexed tree |
| `focus "App"` | Bring app to front |
| `quit "App"` | Quit app |
| `tree --flat` | Read UI tree with indexed elements [1] [2] [3] |
| `tree --flat --app "Teams"` | Read specific app tree |
| `tree --flat --verbose` | Include positions in output |
| `click --index 3` | Click element by index (coordinate-based) |
| `click --label "Send"` | Click element by label |
| `click-at 500 300` | Click at pixel coordinates |
| `double-click --index 3` | Double-click element |
| `right-click --index 3` | Right-click element (context menu) |
| `type "Hello"` | Type text into focused field |
| `type "Hello" --index 5` | Focus element by index, then type |
| `shortcut "cmd+n"` | Keyboard shortcut |
| `scroll down 3` | Scroll wheel down |
| `scroll up 3` | Scroll wheel up |
| `screenshot` | Capture full screen |
| `screenshot --app "Teams"` | Capture app window |
| `hover 500 300` | Move mouse without clicking |
| `drag 100 200 500 400` | Click-drag between points |
| `list-apps` | List installed/running apps |
| `focused-app` | Get focused app name |
| `screen-size` | Get screen dimensions |

## Usage

```bash
openclaw
```

Type natural language commands:
- "Go to amazon.in and search for wireless earbuds"
- "Open Notes and create a new note saying Meeting at 3pm"
- "Get the latest message on Teams"
- "Find iPhone 17 price on Amazon and save it in Notes"

## File Structure

```
omniclaw/
  requirements.txt              # pyobjc dependencies
  README.md                     # This file

  tools/
    macos_accessibility.py      # pyobjc AX layer (indexed elements, coordinate clicking)
    macos_ax.py                 # CLI wrapper (called by OpenClaw exec)

  skills/
    macos-accessibility/
      SKILL.md                  # OpenClaw skill definition

  tests/
    TEST_CASES.md               # Test documentation

~/.openclaw/
  openclaw.json                 # Agent config (GPT-5.4, browser, exec)
  workspace/
    AGENTS.md                   # Operating instructions
    skills/
      macos-accessibility/      # Copy of repo skill
```

## Troubleshooting

**"pyobjc not available"**: Run `pip install -r requirements.txt`

**"Accessibility access denied"**: Grant permission in System Settings -> Privacy & Security -> Accessibility

**"No element registry found"**: Run `tree --flat` before using `click --index`

**Gateway not connecting**: Run `openclaw gateway status` to check. Restart with `openclaw gateway start`.
