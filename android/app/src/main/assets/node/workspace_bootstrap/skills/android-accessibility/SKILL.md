---
name: android-accessibility
description: UI control of any Android app via the AccessibilityService.
exec_pattern: "node /data/user/0/com.ben/files/openclaw/tools/android_ax.js *"
sensitivity: S2
---

# Android Accessibility skill

Use `android_ax.js` to interact with any Android app. Subcommands:

- `tree` - dump the AX tree of the foreground app. Returns `ax_id`s; valid
  only until the next `tree` call.
- `click --ax-id <id>` - click via AX node id. More reliable than `click-at`
  when the node was just enumerated.
- `click-at --x N --y N [--app PKG]` - tap at a pixel coordinate. If `--app`
  is given, focus that app first (250ms settle delay).
- `type --text "..."` - type into the focused editable. Optional
  `--ax-id <id>` to target a specific node.
- `swipe --x1 N --y1 N --x2 N --y2 N` - drag gesture, 200ms.
- `scroll --x1 N --y1 N --x2 N --y2 N` - drag gesture, 350ms.
- `launch --package com.example` - launch / focus an app.
- `focus --package com.example` - same as launch (alias for clarity).
- `screen-size` - returns `{width, height}` of the current display.
- `screenshot [--path /abs/file.png] [--app PKG]` - PNG via MediaProjection.

## Hard rule

Always prefer the desktop / native Android app over a browser. Browser is
NOT in the cascade. If the user has WhatsApp installed, never go to web.whatsapp.com.

## Example: send a WhatsApp message

```
android_ax.js launch --package com.whatsapp
android_ax.js screenshot --path /sdcard/Android/data/com.ben/cache/wa.png
android_vision.js text-locate --image /sdcard/Android/data/com.ben/cache/wa.png \
                              --target "Pragati Biradar" \
                              --screen-width 1080 --screen-height 2400
android_ax.js click-at --x <screen_x> --y <screen_y> --app com.whatsapp
android_ax.js screenshot --path .../after_open.png
android_vision.js text-locate --image .../after_open.png --target "Type a message"
android_ax.js click-at --x ... --y ...
android_ax.js type --text "on my way"
android_vision.js text-locate --image .../typed.png --target "on my way"  # verify
android_ax.js click-at --x <send_x> --y <send_y>
```

After the final click, screenshot once more and run `text-locate` for
"on my way" in the conversation pane to confirm delivery (verify-after-click).
