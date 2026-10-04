# Project Constitution — UG Voice Studio

> How we execute work on this project. These principles guide **everything** we do here.
> Captured 2026-10-02 from the owner's directive.

## 1. Start with the why, and clear every assumption
- Always check our assumptions and **start with the why**.
- Clarify **every single assumption** we are making — and "clarify" means **use research to clear it**, not guess.
- **We update every assumption at planning and design time, NOT at implementation time.** Implementation executes a settled plan; it does not re-open assumptions. Front-load the research (e.g. catching the ElevenLabs streaming limit *before* building on it) so implementation can run heads-down.
- Only once the assumptions are cleared do we move on to building.

## 2. Always use superpowers
- Once assumptions are clear, do the work **using superpowers** — always.

## 3. Isolate disruptive work in a fresh worktree
- Whenever possible, use a **brand-new worktree** if we are disrupting a bunch of working code.

## 4. Parallelize sizable work with a team of agents, in goal mode
- If the size of the work is **sizable**, parallelize it with a **team of sub-agents**.
- Run it in **goal mode**: select the appropriate goal for the work task, then **fire it off automatically**.
- Whenever a goal is set up, aim for the **maximum level of attainment — 10/10** (and if that is too hard, **9/10**).

## 5. The front-end must reflect state
- When an experience touches front-end components (or the front-end is needed to complete the experience),
  make sure the **front-end picks up the change in state and manifests a different view**.
- This can be done as a separate pass or at the end of a section — but it is never skipped.

## 6. Motor along; checkpoint; ask only for keys
- Keep momentum and keep getting things done.
- **Checkpoint the code** (commit) whenever a piece builds locally and passes verification.
- **Only interrupt the owner for things like API keys** (credentials / external enablement). Otherwise, use judgment and proceed.

## 7. Compact at section boundaries
- Before launching a **new section of the codebase or a new task**, **check the context size** first.
- If the context window is **above 50%**, **compact first** before starting the new work. (Externalize state to the SDD ledger / git / docs so the resume after compaction is clean.)

---
*Source: owner directive, 2026-10-02.*
