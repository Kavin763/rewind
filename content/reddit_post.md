# Reddit link post

**Subreddit:** r/aiagents (alternatives: r/LLMDevs, r/AIMemory, r/SideProject)
**Post type:** Link post, pointing to your published article URL

**Title:**
I built an on-call agent that remembers which fixes made outages worse

**First comment (post it yourself right after submitting):**

Author here. The short version: Rewind recalls past incidents from a Hindsight memory bank before it suggests anything. The part that turned out to matter most is storing failed fixes as their own facts ("restarting all payments-api pods at once caused a 20-minute cascade"). That way the agent can warn you before you repeat them.

Same alert with memory off vs on was the clearest test: off, it suggested checking that pods had scaled up (the original cause). On, it cited the earlier incident and put "don't restart all pods at once" at the top.

Happy to answer questions about the retain/recall design or running it on Groq's free tier (rate limits were the painful part). Code: [YOUR GITHUB REPO URL]
