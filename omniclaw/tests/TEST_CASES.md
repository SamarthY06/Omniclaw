# OmniClaw Test Cases — Complex Multi-Step Scenarios

All test cases run on macOS. Each test is a **real-world multi-step command** that
exercises planning, execution, re-planning, cross-app coordination, information
extraction, and error recovery.

Pass criteria verify that OmniClaw:
- Types **visibly** into address bars and search fields
- Reads and summarises content from web pages
- Chains actions across multiple apps
- Extracts information and presents it to the user
- Handles errors gracefully

---

## Sanity Checks (warmup — run first)

### TC-S01: Launch Chrome
**Command:** `"Open Google Chrome"`
**Category:** App Launch
**Pass:** Chrome window appears and is accessible.
**Max time:** 10 s

### TC-S02: Launch + Navigate
**Command:** `"Open Google Chrome and go to google.com"`
**Category:** Browser Nav (basic)
**Pass:** Chrome shows the Google homepage.
**Max time:** 20 s

---

## Complex Multi-Step Scenarios

### TC-C01: Multi-App Research + Write-Up
**Command:** `"Open Chrome, search for the weather in Bangalore, then open Notes and create a new note that says 'Bangalore weather: <what you found>'"`
**Category:** Cross-App Workflow (Browser -> Notes)
**Steps the agent must take:**
1. Launch / focus Chrome
2. Focus address bar (Cmd+L), type "weather in Bangalore", press Enter
3. Wait for results, read the weather summary from the AX tree
4. Launch Notes app
5. Create a new note (Cmd+N)
6. Type the weather information it read from Chrome
**Pass:** Notes contains a new note with weather data.
**Max time:** 60 s

---

### TC-C02: Chained Browser Navigation
**Command:** `"Go to YouTube in Chrome, search for 'how to make pasta', click the first video, then open a new tab and go to amazon.com"`
**Category:** Multi-tab Browser Workflow
**Steps:**
1. Launch / focus Chrome
2. Navigate to youtube.com
3. Search for "how to make pasta"
4. Click the first video result
5. Open a new tab (Cmd+T)
6. Navigate to amazon.com
**Pass:** Two Chrome tabs — one on a YouTube video, one on Amazon.
**Max time:** 60 s

---

### TC-C03: Web Research + Summarise
**Command:** `"Search Google for 'latest macOS Sequoia features' and tell me the 3 most important new features"`
**Category:** Research + Reporting
**Steps:**
1. Launch / focus Chrome
2. Google search for "latest macOS Sequoia features"
3. Read top results from the AX tree
4. Summarise the 3 most important features
5. Return summary to user
**Pass:** Agent response contains 3 distinct feature names/descriptions.
**Max time:** 45 s

---

### TC-C04: Navigate to Specific URL + Extract Data
**Command:** `"Go to github.com/bytedance/deer-flow in Chrome and tell me how many stars the repo has"`
**Category:** Direct URL + Data Extraction
**Steps:**
1. Launch / focus Chrome
2. Navigate to github.com/bytedance/deer-flow
3. Read the star count from the page's AX tree
4. Return the star count to user
**Pass:** Agent response includes a numeric star count.
**Max time:** 45 s

---

### TC-C05: System App + Browser Combo
**Command:** `"Open System Settings, then open Chrome and search for 'how to change Mac wallpaper'"`
**Category:** System + Browser Multi-App
**Steps:**
1. Launch System Settings
2. Confirm it opens
3. Launch / focus Chrome
4. Search for "how to change Mac wallpaper"
**Pass:** System Settings is open AND Chrome shows search results.
**Max time:** 45 s

---

### TC-C06: Sequential Research with Memory
**Command:** `"Open Chrome and search for 'best restaurants in Bangalore'. Remember the top 3 results. Then search for directions to the first one."`
**Category:** Multi-Step Search + Memory
**Steps:**
1. Launch / focus Chrome
2. Search "best restaurants in Bangalore"
3. Read and remember top 3 restaurant names
4. Search "directions to <first restaurant name>"
5. Return the restaurant names and directions info
**Pass:** Agent performs two sequential searches, the second referencing results from the first.
**Max time:** 60 s

---

### TC-C07: Error Recovery — Bad URL
**Command:** `"Open Chrome, go to http://thissitedoesnotexist12345.com, and tell me what happened"`
**Category:** Error Handling + Reporting
**Steps:**
1. Launch / focus Chrome
2. Navigate to the non-existent URL
3. Detect the error page
4. Report back to the user what happened
**Pass:** Agent describes the error (DNS failure, page not found, etc.) without crashing or looping.
**Max time:** 40 s

---

### TC-C08: Create Note, Switch to Chrome, Search, Return to Notes
**Command:** `"Open Notes, create a new note titled 'Shopping List', then open Chrome and search for 'iPhone 16 price in India', then switch back to Notes and add the price you found"`
**Category:** Round-Trip Cross-App
**Steps:**
1. Launch Notes
2. Create new note (Cmd+N), type "Shopping List"
3. Launch / focus Chrome
4. Search "iPhone 16 price in India"
5. Read price from results
6. Switch back to Notes
7. Type the price into the note
**Pass:** Notes contains "Shopping List" with price data.
**Max time:** 75 s

---

### TC-C09: Graceful Failure for Missing App
**Command:** `"Open an app called 'FakeAppThatDoesNotExist2026'"`
**Category:** Error Handling
**Steps:**
1. Agent tries to find / launch the app
2. App not found — re-plan
3. Agent responds with a clear error message
**Pass:** Agent fails gracefully, returns a human-readable error.
**Max time:** 15 s

---

## Performance Benchmarks

| Test    | Target  | LLM Calls Expected | Notes                           |
|---------|---------|---------------------|---------------------------------|
| TC-S01  | < 10 s  | 1                   | Simple launch                   |
| TC-S02  | < 20 s  | 1–2                 | Launch + navigate               |
| TC-C01  | < 60 s  | 2–4                 | Cross-app with screen reading   |
| TC-C02  | < 60 s  | 2–4                 | Multi-tab browser workflow      |
| TC-C03  | < 45 s  | 2–3                 | Search + summarise              |
| TC-C04  | < 45 s  | 2–3                 | URL nav + data extraction       |
| TC-C05  | < 45 s  | 2–3                 | Two apps, sequential            |
| TC-C06  | < 60 s  | 3–5                 | Two searches, memory reference  |
| TC-C07  | < 40 s  | 2–3                 | Error detection + reporting     |
| TC-C08  | < 75 s  | 3–5                 | Round-trip across two apps      |
| TC-C09  | < 15 s  | 1–2                 | Graceful failure                |

---

## Running Tests

```bash
cd omniclaw
python3 tests/run_tests.py                    # run all
python3 tests/run_tests.py --test TC-C01      # run one
python3 tests/run_tests.py --verbose          # detailed output
python3 tests/run_tests.py --sanity           # sanity checks only
python3 tests/run_tests.py --complex          # complex tests only
```
