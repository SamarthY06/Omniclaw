# Use Cases — Comprehensive

The full set of things I should be able to do with my personal assistant. Phrased in natural conversational language, the way I would actually say them. Tagged with sensitivity and the device that should typically execute (overrides allowed in the request itself).

This list is meant to be exhaustive, not prescriptive — the assistant should handle anything in this style, even if a specific phrasing isn't listed.

## Sensitivity legend

- **S0 — safe**: execute immediately, brief audible/visual confirmation
- **S1 — reversible**: execute immediately, notify on completion
- **S2 — important**: confirm in chat or voice before acting
- **S3 — sensitive**: navigate to the screen, hand off to me, never enter the data myself

## Device legend

- **Mac**: laptop is the right executor (filesystem, IDE, large screen, full browser session)
- **Phone**: phone is the right executor (cellular call, SMS, native camera, alarm, on-the-go tasks)
- **Either**: both can do it — default to whichever device I spoke to, unless I override
- **Both**: do it on both in parallel (e.g., DND on every device)
- **Cross**: explicitly cross-device (one device asks, the other executes via delegation)

---

## 1. Communication

### Calls
- "Call mom for me." — S1, Phone
- "Call mom on my phone, I'm on my Mac." — S1, Cross
- "Call the office reception, the number's saved." — S1, Phone
- "Call back the last number that called me." — S1, Phone
- "Decline this incoming call and text them I'll call back in 10." — S2, Phone
- "Mute me on this call." / "Take me off mute." — S0, current call device
- "Put this call on speaker." — S0, current call device
- "Hang up." — S0, current call device

### SMS / messaging
- "Text dad I'll be late, traffic's bad." — S2, Phone
- "WhatsApp Sarah a happy birthday with a cake emoji." — S2, Phone
- "Reply to John's last message saying yes I'll be there." — S2, Either
- "Forward the last photo I took to mom on WhatsApp." — S2, Phone
- "Send the family group a message that I've reached the airport." — S2, Phone
- "Send 'on my way' to all three people I'm meeting." — S2, Either
- "Read me my unread WhatsApps from the family group, I'm cooking." — S0, Phone
- "Read out my last 5 unread Slack messages from #engineering." — S0, Either
- "What did Sarah say in her last message?" — S0, Either
- "Mark all my Slack DMs as read." — S1, Either
- "Pin John's chat on WhatsApp." — S1, Phone

### Email
- "Reply to John's email about the meeting, suggest 3pm tomorrow at my office." — S2, Either
- "Forward the offer letter PDF on my desktop to dad's email." — S2, Mac (file source) + Either (send)
- "Anything urgent in my inbox from this morning, just give me the headlines." — S0, Either
- "Read me the email from Acme Corp." — S0, Either
- "Mark all promotions as read and archive them." — S1, Either
- "Snooze that newsletter for next Sunday." — S1, Either
- "Draft a follow-up to last week's recruiter email, polite decline." — S2, Either
- "Set up a vacation responder from Friday to next Tuesday." — S2, Either
- "Find the email with my flight confirmation for next Tuesday." — S0, Either
- "Search my inbox for the Aadhaar update email from UIDAI." — S0, Either

### Group video / meetings
- "Hop on my 3pm Zoom when it starts, let them know I'll be a minute late." — S2, Mac
- "Join the call with John in 5 minutes." — S1, Mac
- "Mute everyone in this Google Meet, I'm the host." — S2, Mac
- "Record this meeting and save the transcript to my Desktop." — S2, Mac
- "Schedule a 30-minute Zoom with sarah@example.com tomorrow at 4pm." — S2, Either

### Calendar
- "Add Sarah's birthday March 15, recurring every year." — S1, Either
- "What's on my plate today, anything I should be ready for?" — S0, Either
- "What's tomorrow looking like?" — S0, Either
- "Move my 4pm meeting to 5pm and email everyone the update." — S2, Either
- "Find me a 30-minute slot tomorrow afternoon when both me and John are free." — S0, Either
- "Block 9 to 11 every weekday for deep work, recurring." — S1, Either
- "Cancel my 2pm and let everyone know I'm sick today." — S2, Either
- "What did I do last Tuesday from 2pm to 4pm?" — S0, Either
- "Send a calendar invite to dad's email for Sunday lunch at 1pm at home." — S2, Either

### Reminders / tasks
- "Remind me to take my medicine at 9pm tonight." — S1, Phone
- "Remind me to call dad when I leave the office." — S1, Phone (location-based)
- "Add 'fix the auth bug' to my todo list, mark it high priority." — S1, Either
- "What's on my todo list for today?" — S0, Either
- "Mark 'review PR' as done." — S1, Either
- "Move all my unfinished tasks from yesterday to today." — S1, Either
- "Set a 15-minute timer." — S0, Either
- "Cancel that timer." — S0, Either
- "Set an alarm for 5am tomorrow on my phone, I have a flight." — S1, Phone
- "Snooze my morning alarm by 10 minutes." — S0, Phone
- "Delete all my old alarms that aren't from this week." — S1, Phone

---

## 2. Productivity

### Notes / docs
- "Open Notes and jot down: meeting with John 3pm tomorrow, agenda is the Q3 plan." — S1, Either
- "Add 'check the deployment scripts' to my work notes." — S1, Either
- "Find that note I wrote about the React migration last month." — S0, Either
- "Find the note where I jotted down the wifi password from the office." — S0, Either
- "Create a Notion page in Projects called Q1 Planning, just an empty one." — S1, Either
- "Summarize section 4 of the PDF that's open on my screen." — S0, Mac
- "Extract all the action items from this meeting transcript." — S0, Either
- "Translate this paragraph to Hindi and copy it to my clipboard." — S0, Either
- "Rewrite this email I'm drafting in a more formal tone." — S0, Either
- "Proofread this paragraph and tell me what's wrong." — S0, Either

### Files
- "Find that PDF on my desktop called offer letter, the one from Acme." — S0, Mac
- "Open the latest file in my Downloads folder." — S0, Mac
- "Move all the screenshots from Desktop into Pictures > Screenshots." — S1, Mac
- "Compress this folder and email it to dad." — S2, Mac
- "Rename all the files in this folder to add today's date as a prefix." — S2, Mac
- "Delete every file in Downloads older than 30 days." — S2, Mac
- "Find every PDF in my entire Mac that mentions 'tax 2025'." — S0, Mac
- "Show me how much disk space I have left." — S0, Mac
- "Empty my Trash." — S2, Mac
- "Find that photo I took at the beach last summer." — S0, Phone
- "Move my last 10 photos to a new album called 'Diwali'." — S1, Phone
- "Delete the screenshot I just took." — S1, Either
- "Take a screenshot of this window and copy it to my clipboard." — S0, Mac

### Quick capture
- "Take a photo of this whiteboard with my phone, do it remotely from here." — S1, Cross
- "Take a photo with my phone front camera." — S1, Phone
- "Record a 10-second video of what's on my desk." — S1, Phone
- "Scan this document with my phone's camera and email it to me." — S1, Phone
- "Save the URL of every tab I have open right now to my notes." — S1, Mac

---

## 3. Commerce

### Shopping
- "Add an iPhone 17 to my Amazon cart, the 256GB blue one." — S1, Either
- "Find me the cheapest noise-cancelling headphones under 5000 rupees, look on Amazon and Flipkart, give me the top 3." — S0, Either
- "Compare the Sony WH-1000XM5 across Amazon, Flipkart, and Croma." — S0, Mac
- "Reorder the same protein powder I bought last month." — S2, Either
- "Track my latest Amazon order, when's it arriving?" — S0, Either
- "Cancel my Myntra order from last night if it hasn't shipped." — S2, Either
- "Show me my last 5 Amazon orders." — S0, Either
- "Add to my Amazon wishlist: that Bluetooth speaker JBL Charge 5." — S1, Either
- "Buy this thing I'm looking at." — S3, current device — pause for confirmation

### Food delivery
- "Order biryani from the place I had it from last Friday on Swiggy." — S2, Phone
- "What did I order last time on Zomato?" — S0, Phone
- "Order my usual breakfast from the Cafe Coffee Day near office." — S2, Phone
- "Cancel my Swiggy order if it hasn't been picked up yet." — S2, Phone
- "Show me what's on the menu at Truffles, Indiranagar branch." — S0, Either
- "Where's my food right now? How long?" — S0, Either

### Cabs / travel
- "Book me an Uber to office." — S2, Phone
- "Book the cheapest cab from here to the airport." — S2, Phone
- "What's the cheapest Ola right now to my home?" — S0, Phone
- "Cancel my Uber if I haven't been picked up yet." — S2, Phone
- "Where's my driver right now?" — S0, Phone
- "Book me a flight from Bangalore to Delhi for next Friday morning, cheapest." — S3, Either — find options, hand off
- "Check me in for tomorrow's IndiGo flight." — S3, Either
- "Add my upcoming flight to my calendar." — S1, Either

### Bills / payments (always sensitive)
- "Pay the electricity bill." — S3, Either
- "Pay my Airtel postpaid bill." — S3, Either
- "Send 500 rupees to mom on UPI." — S3, Phone
- "What was my last credit-card bill?" — S0, Either
- "Show me my pending bills due this week." — S0, Either
- "Set up an autopay reminder for my electricity bill." — S1, Either

---

## 4. Media & entertainment

### Music
- "Play my morning playlist on Spotify, the chill one." — S0, Either
- "Continue what I was listening to last night." — S0, Either
- "Play 'Bohemian Rhapsody'." — S0, Either
- "Play some lo-fi beats." — S0, Either
- "Skip this song, not feeling it." — S0, current playback device
- "Pause." / "Resume." / "Volume up a bit." / "Mute." — S0, current playback device
- "What's this song? Shazam it." — S0, Phone
- "Add this song to my favorites." — S1, current playback device
- "Switch the audio output to my AirPods." — S0, current playback device
- "Move the music from my phone to my Mac." — S0, Cross

### Video / streaming
- "Continue Stranger Things on Netflix from where I left off." — S0, Either
- "Open YouTube and play the latest video from Veritasium." — S0, Either
- "Play that video I bookmarked yesterday." — S0, Mac
- "Cast this YouTube video to the TV." — S0, Phone
- "Pause the TV." — S0, Phone (TV remote app)

### Podcasts / audiobooks
- "Continue the podcast I was listening to in the car." — S0, Phone
- "Subscribe me to Lex Fridman's podcast." — S1, Phone
- "Skip ahead 30 seconds." — S0, current playback device

---

## 5. Information & research

### Quick info
- "What's the weather looking like tomorrow, should I bring a jacket?" — S0, Either
- "What's the forecast for the rest of the week?" — S0, Either
- "Read me today's tech headlines, top 5." — S0, Either
- "What's AAPL trading at right now? How's it done over the past month?" — S0, Either
- "What time is it in San Francisco right now?" — S0, Either
- "What's 47 times 89?" — S0, Either
- "Convert 250 USD to INR at today's rate." — S0, Either

### Research
- "Find me a recipe for paneer tikka, something simple, serves two." — S0, Either
- "Compare the Tesla Model 3 and the BYD Seal — range, price, charging time, in a table." — S0, Mac
- "What does this medical term in this report mean? Explain in simple language." — S0, Either
- "Find me 3 good blog posts on Kubernetes operators, recent ones." — S0, Mac
- "Brief me on what I worked on this week, pull from my notes and the OmniClaw repo." — S0, Mac
- "Summarize the news on the latest RBI rate decision." — S0, Either

### Translate / explain
- "Translate this menu (photo) to English." — S0, Phone
- "How do I say 'thank you' in Japanese?" — S0, Either
- "Explain this code snippet to me." — S0, Mac
- "What does this error message mean?" — S0, Mac

---

## 6. Travel & navigation

- "Navigate me home." — S1, Phone
- "Navigate me to office, avoid tolls." — S1, Phone
- "What's the traffic like to the airport right now?" — S0, Phone
- "Find me the nearest petrol pump that's open." — S0, Phone
- "Find a parking spot near MG Road." — S0, Phone
- "Set a destination on Google Maps and send it to my phone." — S1, Cross
- "What's the metro fare from MG Road to Whitefield?" — S0, Either
- "Plan a 5-day trip to Goa for me, suggest hotels and itinerary." — S0, Mac

---

## 7. Health & fitness

- "Log that I walked 5km today." — S1, Phone
- "What's my step count today?" — S0, Phone
- "Remind me to drink water every 90 minutes today." — S1, Phone
- "Track that I had 2 idlis and a coffee for breakfast." — S1, Phone
- "What did I weigh last week?" — S0, Phone
- "Set a meditation timer for 15 minutes." — S0, Phone
- "Tell me my heart rate average for last week." — S0, Phone
- "Schedule my next dental cleaning, find a slot." — S2, Either

---

## 8. Smart home (when applicable)

- "Turn off the bedroom lights." — S0, Either
- "Set the AC to 24 degrees." — S0, Either
- "Lock the front door." — S2, Either
- "Show me the front door camera." — S0, Either
- "Run my 'goodnight' routine." — S1, Either
- "Tell me what the indoor temperature is." — S0, Either

---

## 9. System & device control

### Mac
- "Open my IDE in the OmniClaw project." — S0, Mac
- "Restart Bluetooth, my AirPods aren't connecting." — S1, Mac
- "What's eating my battery right now?" — S0, Mac
- "Force quit Slack, it's hung." — S1, Mac
- "Switch my Mac to dark mode." — S0, Mac
- "Set Do Not Disturb for the next hour." — S1, Mac
- "Mirror my Mac to the TV." — S1, Mac

### Phone
- "Toggle DND." — S0, Phone
- "Turn on hotspot." — S1, Phone
- "Turn airplane mode on for 30 minutes." — S1, Phone
- "Turn the brightness up to max." — S0, Phone
- "Connect to home wifi." — S1, Phone
- "Pair my AirPods." — S1, Phone
- "Switch to vibrate-only." — S0, Phone
- "Open the camera in selfie mode." — S0, Phone

### Cross
- "Turn on Do Not Disturb on both my devices, I'm in a meeting." — S1, Both
- "Mirror what's on my phone to my Mac." — S0, Cross
- "AirDrop the file I'm looking at on my Mac to my phone." — S0, Cross — actually executed on Mac, received on Phone
- "Lock my Mac when I leave the room." — S2, Mac (proximity-based)

### App management
- "Is Swiggy installed on my phone?" — S0, Phone
- "Install Notion on my phone." — S2, Phone (opens Play Store, I tap install)
- "Update WhatsApp." — S2, Phone
- "Uninstall the apps I haven't opened in 30 days." — S2, Phone (asks which ones first)
- "What apps am I using the most this week?" — S0, Phone

---

## 10. Cross-device coordination

- "Find the offer letter PDF on my Mac and send it to me on WhatsApp." — Cross — Mac finds + Phone sends
- "Take a photo with my phone right now, even though I'm on my Mac." — Cross — Phone executes
- "Read out my unread Slack messages on my Mac, but speak through my phone speaker." — Cross — Mac reads, Phone TTS
- "Continue this YouTube video on my phone." — Cross — Mac stops, Phone resumes
- "I'm leaving for office. Set DND on the Mac, queue my drive playlist on the phone, and silence Slack notifications until 7pm." — Cross + Both — multiple things at once on multiple devices

---

## 11. Standing orders / autonomous (no prompt needed)

- Morning brief at 7am: overnight emails I care about, today's calendar, weather, anything urgent.
- Evening wrap at 9pm: summary of what I did, what's pending tomorrow, set tomorrow's morning briefing context.
- 15 minutes before each calendar meeting: brief heads-up with relevant docs, recent emails with attendees, who's on the invite, the link.
- Continuous email triage: auto-archive promo, label by topic (work, personal, finance, transactional), draft replies for routine I can review.
- Bill watcher: detect bill emails, add to calendar, remind me 2 days before due.
- Birthday reminder: 7 days and 1 day before each contact's birthday, suggest something.
- Weekly summary Sunday 6pm: work + spend + comms summary.
- Failed-task retry: re-attempt cron jobs that errored; escalate to me after 2 retries.
- Stale-thing surfacing during idle time: "you have an unread email from your CA from 4 days ago", "you have a pending Amazon return that expires Friday".
- Location-based: "you've reached office" / "you've left home" → optional actions I configured.
- Battery / network awareness: pause heavy phone tasks when battery <20% and not charging; queue them for later.

---

## 12. Memory and learning behaviors

- "Remember that mom's name is Sarah Roy and her number is +91xxxxxxxxxx." — S1, learned permanently
- "Remember that I prefer aisle seats on flights." — S1, learned permanently
- "Always set DND on both devices when I have a calendar event titled 'focus'." — S1, learned rule
- "When I order biryani, default to the Truffles place in Indiranagar." — S1, learned preference
- "Stop reading me promo emails, they're never interesting." — S1, learned filter
- "Whenever I leave office, remind me to call dad." — S1, learned trigger
- "Forget that I asked you to do X." — S1, removes a learned preference

---

## 13. Sensitive (always pause, never auto-execute)

- Payment info entry — card numbers, CVV, UPI PIN, NetBanking password
- OTP / 2FA codes — never read, never enter
- Passwords / PINs / Aadhaar / SSN / passport numbers
- Account deletion — confirm twice
- First-time external recipient — confirm before sending
- Money transfer above my configured threshold — confirm
- Government / legal forms
- Anything platform marks as "permanent" or "irreversible"
- Biometric / Face ID / fingerprint actions
- Permission grants — installing apps, granting accessibility, granting screen recording

---

## 14. Failure modes & graceful degradation

- "What can you not do right now?" — list current limitations (e.g., "Mac is asleep so cross-device file requests will fail")
- "Try the last thing again, the network was bad." — S1, retry with same intent
- "Stop everything." — S0, immediate halt of all in-flight tasks
- "Cancel the current task." — S0, halt the current task only
- "What did you just do?" — S0, explain the last action
- "Undo the last thing." — S2, attempt reverse if reversible

---

## 15. Conversation control

- "Be more brief." / "Talk less." / "Shorter answers." — S0, set tone
- "Use a more formal tone." — S0, set tone
- "Stop calling me 'sir', just use my name." — S1, learned preference
- "Switch your name to Aria." — S1, identity change
- "Change the wake word to 'Aria'." — S1, identity change
- "Mute yourself for the next hour." — S0, suppression
- "Speak in Hindi from now on." — S1, language preference

---

## 16. Out of scope (intentional)

The assistant should explicitly decline:
- Spoofing identity
- CAPTCHA bypass at scale
- Anything explicitly against an app's ToS at scale
- Privacy-sensitive data of others without consent
- Self-modifying its own operating rules without my approval
- Acting on behalf of someone other than me without explicit handoff
