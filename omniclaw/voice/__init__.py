"""Always-on voice subsystem for the Mac peer of Ben.

Modules:

* `wake_phrase_matcher` - exact mirror of WakePhraseMatcher.kt and
  src/wake/phrase_matcher.js. Same Damerau-Levenshtein with at most
  MAX_TOKEN_EDITS=1 per token and MAX_TOTAL_EDITS=2 across the matched
  window. Keep all three implementations in sync if you tune one.

* `wakeword_mac` - SFSpeechRecognizer-driven always-on listener that runs
  via launchd, fuzzy-matches the configured phrase, and on detection execs
  start_voice.sh to open the existing OpenClaw realtime entry.
"""
