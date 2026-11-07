# Video script: Rewind (about 3 minutes)

Record at 1080p or higher. Before recording, double-click `start.bat`, wait until Rewind opens at http://localhost:8000, and increase your browser zoom to 125% so the text is readable. Don't read this word for word; use it as a guide.

## 1. Intro (0:00–0:30)

**On screen:** your face (webcam) or the Rewind console with the sidebar of past incidents.

> Hi, I'm [YOUR NAME]. This is Rewind, an on-call agent with long-term memory. When you get paged, it remembers how similar incidents were fixed. More importantly, it remembers which fixes made things worse, so you don't repeat them. Memory comes from Hindsight, running locally next to the app.

## 2. The problem (0:30–1:00)

**On screen:** click the **payments-api pool exhausted** sample. Switch **Memory off**. Click **Analyze**.

> Here's a real-looking page: payments-api is failing 29% of charges, the database is out of connection slots, and there was a deploy 13 minutes ago. With memory off, the model gives decent but generic advice: roll back, check that the pods scaled up to 18. Scaling up is actually what caused this last time. It has no way of knowing that.

**Point at:** the grey "Memory was off" banner and the generic steps.

## 3. Demo (1:00–2:30)

**On screen:** switch **Memory on**. Click **Analyze** again.

> Same alert, memory on. Rewind ran two recalls against Hindsight in parallel: what fixed incidents like this, and what failed. It recognises INC-2041 and INC-2067. Look at this red box: don't restart all pods at once. Last time that caused a 20-minute cascade.

**Hover** over a citation chip (like M5) so the memory on the right highlights.

> Every step cites the memory it came from. If the model invents an incident or a citation, the code drops it.

**On screen:** click the **billing-cron stuck (no history)** sample. Click **Analyze**.

> Now something it has never seen. Billing-cron is stuck in Pending. Low confidence, generic Kubernetes advice. Fair enough. Let's teach it.

**On screen:** scroll to "Close the loop". Fill in:
- Root cause: *invoice-generate requests 12Gi; after the node pool shrank no node had 12Gi free*
- Worked: *Lowered the memory request to 4Gi and pinned the job to the batch node pool*
- Failed: *Deleted the pending job: the CronJob recreated it* and *Added a node: nodes have 8Gi, the pod needs 12Gi*

Click **Save to memory**.

> That retain goes into Hindsight's queue. It extracts each fix and each failed fix as its own fact.

**Cut or wait about two minutes**, then click the billing-cron sample again and **Analyze**.

> Same alert again. Confidence high, seen before in INC-2103, the 4Gi fix first, and both failed attempts in the red box. One resolution, and the next engineer starts where the last one finished.

**Optional (if your Groq quota allows):** open **Ask the memory** and ask *"Which fixes have made incidents worse?"* This uses Hindsight's reflect to reason over the whole memory bank.

## 4. Wrap-up (2:30–3:00)

**On screen:** your face, or `app/memory.py` open at `failure_text`.

> What surprised me most: storing failures is more valuable than storing fixes. The trick is writing each failed fix as its own short warning that names the service, so retrieval actually finds it. Code is linked below, and Hindsight is open source. Thanks for watching.

## YouTube titles (pick one)

1. My AI On-Call Agent Remembers the Fixes That Failed
2. I Gave My Incident Agent Memory. Here's the Before and After
3. This Agent Stops You Repeating Last Month's Outage Mistake
4. Same Alert, Two Answers: An On-Call Agent With and Without Memory
5. Building an Incident Agent That Learns From Every Post-Mortem (Hindsight)

## Description (paste under the video)

Rewind is an on-call incident agent with long-term memory. It recalls what fixed similar incidents and, more importantly, which fixes made things worse, then warns you before you repeat them. Memory: Hindsight (self-hosted). LLM: Groq.

Code: [YOUR GITHUB REPO URL]
Article: [YOUR ARTICLE URL]
Hindsight: https://github.com/vectorize-io/hindsight
Docs: https://hindsight.vectorize.io/
