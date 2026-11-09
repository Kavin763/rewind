# Reddit post (published as a text post in r/aiagents)

r/aiagents does not allow bare self-promotion links, so this went up as a text post with the links at the end.

**Title:** I built an on-call agent that remembers which fixes made outages worse

**Flair:** the closest to Project / Showcase / Discussion

**Body:**

Most incident tools (and most agents) remember what fixed a problem. I wanted to see what happens if an on-call agent also remembers what made things worse.

How it works: you paste an alert, and before the LLM says anything the agent runs two recalls against a Hindsight memory bank, one for "what fixed incidents like this" and one for "what failed or made them worse". Every failed fix is stored as its own short fact that names the service and the incident, e.g. "restarting all payments-api pods at once caused a 20-minute cascade". Recalled memories are numbered, the model has to cite them, and any citation it can't back with a real memory gets dropped in code.

Same alert, memory off vs on:
- Off: "roll back, make sure pods scaled up to 18" (scaling up was the original cause)
- On: "seen before in INC-2041, don't restart all pods at once, last time that caused a 20-minute cascade"

Then I gave it an alert it had never seen (a CronJob stuck in Pending), logged how it was actually fixed plus two things that didn't work, and ran the same alert again. Second time it was high confidence and warned against both failed attempts.

Biggest lesson: the memory layer has its own LLM cost. On Groq's free tier, writing 3-4 memories per incident hit the token limits fast. Writing one post-mortem per incident, with each fix and failure as its own sentence, fixed it.

Write-up with screenshots: https://medium.com/@kaviselvank4/my-on-call-agent-uses-hindsight-to-remember-fixes-that-failed-0073a00cfdc6
Code: https://github.com/Kavin763/rewind

Curious if anyone else stores negative outcomes explicitly in agent memory, or how you handle "don't do X" knowledge.
