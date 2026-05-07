# OmniClaw Test Cases — v2.0

All tests run on macOS and use **real system-state verification** (not just
keyword matching). Each verifier checks actual OS state: running processes,
window titles, clipboard content, Notes body, or response analysis.

---

## Validation Strategy

| Verifier | What it checks |
|----------|---------------|
| `verify_app_running(name)` | `pgrep` confirms the process is alive |
| `verify_chrome_title_contains(sub)` | AppleScript reads Chrome's active tab title |
| `verify_notes_contains(sub)` | AppleScript reads first note body from Notes.app |
| `verify_clipboard_contains(sub)` | `pbpaste` reads the macOS clipboard |
| `verify_response_contains_any(kw)` | Agent response includes expected keywords |
| `verify_response_has_content(n)` | Agent response is ≥ n characters (not just "Done!") |

---

## Sanity Checks

### TC-S01: Launch Chrome
**Command:** `"Open Google Chrome"`
**Verify:** Chrome process is running + response acknowledges launch.
**Max time:** 15s

### TC-S02: Launch + Navigate
**Command:** `"Open Google Chrome and go to google.com"`
**Verify:** Chrome process running + active tab title contains "google".
**Max time:** 25s

---

## Complex Multi-Step Scenarios

### TC-C01: Multi-App Research + Notes
**Command:** `"Open Chrome, search for the weather in Bangalore, then open Notes and create a new note..."`
**Verify:** Chrome running + Notes running + response keywords.
**Max time:** 60s

### TC-C02: YouTube + Amazon (multi-tab)
**Command:** `"Go to YouTube in Chrome, search for 'how to make pasta', click first video, open new tab and go to amazon.com"`
**Verify:** Chrome running + response keywords.
**Max time:** 60s

### TC-C03: Research + Summarise
**Command:** `"Search Google for 'latest macOS Sequoia features' and tell me the 3 most important"`
**Verify:** Response has substantial content (≥80 chars) + contains relevant keywords.
**Max time:** 50s

### TC-C04: GitHub Data Extraction
**Command:** `"Go to github.com/bytedance/deer-flow and tell me how many stars"`
**Verify:** Response has content + contains relevant keywords.
**Max time:** 50s

### TC-C05: System Settings + Chrome
**Command:** `"Open System Settings, then open Chrome and search for 'how to change Mac wallpaper'"`
**Verify:** System Settings running + Chrome running + response keywords.
**Max time:** 50s

### TC-C06: Sequential Search with Memory
**Command:** `"Search 'best restaurants in Bangalore'. Remember top 3. Then search directions to the first one."`
**Verify:** Chrome running + response keywords.
**Max time:** 70s

### TC-C07: Error Recovery (bad URL)
**Command:** `"Open Chrome, go to http://thissitedoesnotexist12345.com, tell me what happened"`
**Verify:** Response describes the error condition.
**Max time:** 45s

### TC-C08: Round-trip Notes → Chrome → Notes
**Command:** `"Open Notes, create 'Shopping List', search iPhone 16 price in Chrome, switch back to Notes and add the price"`
**Verify:** Notes running + Chrome running + response keywords.
**Max time:** 80s

### TC-C09: Graceful Failure (fake app)
**Command:** `"Open 'FakeAppThatDoesNotExist2026'"`
**Verify:** Response contains error/failure language.
**Max time:** 20s

---

## Intent-Based Natural Language Tests (NEW)

These tests use **pure natural language** — no explicit app or action names.
The planner must infer the full workflow.

### TC-I01: E-Commerce Shopping
**Command:** `"Search for korean trousers for men on Amazon and add the first result to cart"`
**Verify:** Chrome running + response mentions amazon/trouser/cart.
**Max time:** 90s

### TC-I02: Research → Notes
**Command:** `"Find the top 3 tourist places in Bali and save them in a note"`
**Verify:** Notes running + response mentions bali/tourist keywords.
**Max time:** 80s

### TC-I03: YouTube Music
**Command:** `"Play the latest Coldplay music video on YouTube"`
**Verify:** Chrome running + response mentions youtube/coldplay.
**Max time:** 70s

### TC-I04: GitHub Trending
**Command:** `"Go to GitHub, find the trending Python repos, and tell me the top 3"`
**Verify:** Response has substantial content + mentions github/python/trending.
**Max time:** 70s

### TC-I05: Price Comparison
**Command:** `"Search for iPhone 16 Pro price on Amazon and Flipkart, tell me which is cheaper"`
**Verify:** Response has content + mentions both sites and price.
**Max time:** 100s

### TC-I06: WhatsApp Messaging
**Command:** `"Open WhatsApp and send 'Good morning' to the first chat"`
**Verify:** Response mentions whatsapp/morning/send/confirm.
**Max time:** 50s

### TC-I07: Sensitive Action (pizza order)
**Command:** `"Order a pizza from Dominos"`
**Verify:** Response asks for confirmation or explains it can't proceed with payment.
**Max time:** 60s

---

## Performance Benchmarks

| Test | Target | Expected LLM Calls |
|------|--------|---------------------|
| TC-S01 | < 15s | 1 |
| TC-S02 | < 25s | 1–2 |
| TC-C01 | < 60s | 2–4 |
| TC-C02 | < 60s | 2–4 |
| TC-C03 | < 50s | 2–3 |
| TC-C04 | < 50s | 2–3 |
| TC-C05 | < 50s | 2–3 |
| TC-C06 | < 70s | 3–5 |
| TC-C07 | < 45s | 2–3 |
| TC-C08 | < 80s | 3–5 |
| TC-C09 | < 20s | 1–2 |
| TC-I01 | < 90s | 4–6 |
| TC-I02 | < 80s | 3–5 |
| TC-I03 | < 70s | 3–5 |
| TC-I04 | < 70s | 3–5 |
| TC-I05 | < 100s | 5–8 |
| TC-I06 | < 50s | 2–4 |
| TC-I07 | < 60s | 2–3 |

---

## Running

```bash
cd omniclaw
python3 tests/run_tests.py                    # run all (18 tests)
python3 tests/run_tests.py --sanity           # sanity only (2)
python3 tests/run_tests.py --complex          # complex only (9)
python3 tests/run_tests.py --intent           # intent-based NL only (7)
python3 tests/run_tests.py --test TC-I01      # single test
python3 tests/run_tests.py --verbose          # full output + check details
```
