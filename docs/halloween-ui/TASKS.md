# Halloween UI — Build Plan (GitHub-issue-ready)

**Status:** done; the theme shipped. Kept as the record of the build plan.

Each task below is a self-contained, independently-reviewable unit: crisp title, scope, exact files,
acceptance criteria, and a browser devloop. Build from `DESIGN.md`. Bite-sized, DRY, YAGNI.

---

## How to devloop this theme (shared harness — read once)

The theme is driven entirely by `body[data-voice-mode="halloween"]`, so you can test **without a live
voice call**. In the running studio (served locally), use the browser console / an
`evaluate_script`:

```js
// ENTER full theme:
document.body.dataset.voiceMode = "halloween";
// EXIT (must leave zero residue):
document.body.dataset.voiceMode = "standard";   // or: delete document.body.dataset.voiceMode
```

Toggle `document.documentElement.dataset.theme` between `"dark"` and `"light"` to check both palettes.
Emulate reduced motion (Chrome DevTools: Rendering → "Emulate prefers-reduced-motion: reduce", or the
`emulate`/`evaluate_script` MCP tools) for every motion AC. Take before/after screenshots across a full
enter→exit cycle for the revert ACs.

**Every CSS task shares `studio.css`** → the `studio.css` tasks are **SEQUENTIAL** (one branch, ordered
commits; or serialize branches to avoid conflicts). Tasks on *other* files run in **PARALLEL**.

---

## Dependency & parallelism map

```
T0 ──> T1 ──> T2 ──> T4 ──> T5 ──> T6 ──> T7 ──> T8 ──> T9a ──> T10        (studio.css spine, SEQUENTIAL)
          \                 ^
           \                | (styles the DOM from T3)
            └── T3 ─────────┘                                              (index.html — PARALLEL, prereq for T4)
                 T9b                                                       (studio.js — PARALLEL)
   T11  (agent.py / agent_prompt.py / voice_mode.py — FULLY PARALLEL, independent of all CSS)
   ──────────────────────────────────────────────────────────> T12 (QA, FINAL, after everything)
```

**Can start immediately in parallel:** T3 (index.html), T9b (studio.js), **T11 (agent-side bridge)**.
**Sequential on `studio.css`:** T0 → T1 → T2 → T4 → T5 → T6 → T7 → T8 → T9a → T10.
**Final gate:** T12 (full QA; depends on all).

---

## T0 — Palette token foundation (dark + light Halloween)

- **Scope:** Add the scoped Halloween palette: re-declare the studio's existing CSS custom properties under
  `body[data-voice-mode="halloween"]` (dark "Midnight") and
  `:root[data-theme="light"] body[data-voice-mode="halloween"]` (light "Harvest"), per DESIGN.md §1. This is
  the foundation that reskins ~70% of the studio in one cascade. Keep the four pillar hues and `--danger`
  unchanged. Reuse the existing `--hw-orange/--hw-violet/--hw-ink` tokens as the signature hues.
- **Files:** `app/web/public/studio.css` (new scoped block, after the existing light-theme block /
  Voice-Mode section).
- **Acceptance criteria:**
  - Entering Halloween swaps the whole studio palette cohesively (surfaces → midnight-violet, Lava →
    pumpkin) in **both** dark and light themes.
  - Standard studio and `architecture.html` are visually **unchanged** (rules are scoped; nothing leaks).
  - Every themed `--text-*` and `--accent-text` clears **WCAG AA** on `--surface`, `--surface-3`, and
    `--ink-2` in both themed palettes (verify with a contrast tool).
  - **Reverts cleanly to standard:** removing the attribute restores the exact standard palette.
  - **Reduced-motion respected:** N/A for motion here, but the swap stays instant and legible under reduce.
- **Verify (devloop):** set/remove `data-voice-mode="halloween"`; eyeball all three stages; flip
  `data-theme`; run a contrast check on sampled text/surface pairs; confirm `architecture.html` unchanged.

## T1 — Hero motif + "free" accent audit under the new tokens

- **Scope:** Verify and, where needed, nudge the elements that reference `--accent`/pillar hues so they read
  well once pumpkin replaces Lava — especially the hero motif (`.hm-wave`, `.hm-route--active`,
  `.hm-node--active`, shield `--c-control`), primary buttons, connected chip, transcript agent/caller
  bubbles, cost meter/sparkline. Most are **free** via T0; this task is the deliberate pass to catch any
  pairing that now reads muddy (e.g., pumpkin-on-violet focus ring contrast) and fix only those.
- **Files:** `app/web/public/studio.css`.
- **Acceptance criteria:**
  - Hero motif, CTAs, connected chip, transcript bubbles, cost meter/sparkline all read crisply in themed
    dark **and** light; no muddy or low-contrast accent pairing.
  - No regression to standard (changes are either scoped to Halloween or are hue-neutral).
  - **Reverts cleanly to standard.** **Reduced-motion respected** (no new animation introduced).
- **Verify (devloop):** enter Halloween on the Configure stage (hero motif visible) and a simulated Live
  stage; screenshot dark + light; confirm the chosen-route/node and shield still read as intended.

## T2 — Whole-studio enter/exit cross-fade

- **Scope:** Add `transition: background-color var(--dur-3) var(--ease-signature), border-color … , color …`
  to the major surfaces that don't already transition them (body + `.bg`, `.config-card`, `.pillar`,
  `.rail-card`, `.transcript-card`, `.summary-card`/`.fail-card`, inputs/textarea, `.caller`, `.ask-chip`,
  bubbles) so the token swap in T0 reads as a ~420ms cross-dissolve on **both** enter and exit. Color/
  background/border only — never box-shadow/filter.
- **Files:** `app/web/public/studio.css`.
- **Acceptance criteria:**
  - Enter and exit are a smooth cross-fade, not a jump, in both themes.
  - No flash on initial page load (transitions must not fire on first paint).
  - **Reverts cleanly to standard** (same transition animates back).
  - **Reduced-motion respected:** the existing blanket `* { transition-duration:.001ms }` neutralizes the
    cross-fade → instant, legible swap. Confirm no residual animation.
- **Verify (devloop):** toggle the attribute and watch the dissolve; reload the page in standard and confirm
  no fade on load; emulate reduced motion and confirm instant swap.

## T3 — Atmosphere DOM scaffold + inline motifs  *(PARALLEL — index.html)*

- **Scope:** Add one additive, `aria-hidden="true"` container `.hw-atmos` to `index.html` (near `.bg`),
  holding the atmosphere nodes: moon, 2 fog bands, 6–8 ember spans, 2–3 reused inline-SVG bats, 1–2 inline-
  SVG cobweb corners (authored SVG per DESIGN.md §3/§9). No styling here beyond structure/class hooks
  (styling is T4). Element must be inert/invisible until themed.
- **Files:** `app/web/public/index.html`.
- **Acceptance criteria:**
  - `.hw-atmos` exists with the motif children, `aria-hidden`, no inline styles.
  - With no CSS yet it has zero visible/interaction effect in standard (will be `display:none` via T4;
    until then it must not cover or shift content).
  - Markup adds no accessibility-tree entries (decorative only); `architecture.html` is **not** touched.
- **Verify (devloop):** load the studio; confirm no visual/layout change and nothing in the a11y tree;
  inspect the DOM for the scaffold.

## T4 — Atmosphere styling + loops (fog, moon, embers, bats, cobweb, nightfall)  *(after T0 + T3)*

- **Scope:** Style `.hw-atmos` and its children, all scoped under `body[data-voice-mode="halloween"]`:
  `display:none` in standard; reveal + `opacity 0→1` on enter; escalate `.bg::before` nightfall gradient;
  moon (static radial); fog drift (`translateX`, long de-synced loop); embers (`translateY`+`opacity`,
  staggered); bats (`translate`+micro-`rotate`); static cobweb. Transform/opacity only; static blur;
  `will-change` scoped under Halloween. Add the matching reduced-motion rules (freeze/omit motion; hide
  drifting embers/bats).
- **Files:** `app/web/public/studio.css`.
- **Acceptance criteria:**
  - Atmosphere fades in on enter and reads as a tasteful, sparse, crafted scene (density per DESIGN.md §3).
  - All motion is transform/opacity; **no animated box-shadow/filter/layout**; blur is static.
  - **Reverts cleanly to standard:** `.hw-atmos` returns to `display:none`/`opacity:0`; all loops stop; no
    promoted layers linger (check Layers panel).
  - **Reduced-motion respected:** no fog/ember/bat motion under reduce; a static themed frame remains;
    drifting embers/bats hidden.
  - Standard studio shows nothing; `architecture.html` unaffected.
- **Verify (devloop):** enter/exit; watch fade + drift; open DevTools Layers to confirm no leftover
  compositor layers after exit; emulate reduced motion and confirm stillness.

## T5 — Topbar, hero & Configure skin

- **Scope:** Escalate the topbar (violet underline + pumpkin glow — build on the existing rule), brand-word
  glow, stepper active tint; hero display-heading glow treatment (uses `--font-display`, see T9); confirm
  Configure card/form/presets/CTAs read well. Mostly polish on top of T0's free retint.
- **Files:** `app/web/public/studio.css`.
- **Acceptance criteria:**
  - Topbar/hero/Configure read as a cohesive themed scene in both themes; text legible.
  - **Reverts cleanly to standard.** **Reduced-motion respected** (glow is static; no animation added).
- **Verify (devloop):** enter on Configure; screenshot dark + light; confirm form inputs/focus ring legible.

## T6 — Generate stage skin

- **Scope:** Theme the timeline (connector gradient pumpkin→violet, active/done node accents), summary and
  fail cards. Keep stat/tier numerals in DM Sans/mono. Build on T0's free retint; add only what the stage
  needs.
- **Files:** `app/web/public/studio.css`.
- **Acceptance criteria:**
  - Generate stage reads themed and cohesive; the timeline's active/done states remain clearly
    distinguishable; data numerals unchanged.
  - **Reverts cleanly to standard.** **Reduced-motion respected** (the existing active-pulse already honors
    reduce; add nothing that violates it).
- **Verify (devloop):** force the Generate stage visible (unhide `#stage-generate` in devtools), enter
  Halloween, step the timeline states; screenshot dark + light.

## T7 — Live console & pillars skin

- **Scope:** Theme the Live grid: rail/call-card, caller avatars/checks, mic-signal + mic-meter, ask-chips,
  all four pillars' surfaces (keep top hue lines), transcript bubbles + live dot, Costs meter/sparkline.
  Mostly T0-free; add polish + any contrast fixes. (Control-pillar centerpiece is T8.)
- **Files:** `app/web/public/studio.css`.
- **Acceptance criteria:**
  - Live console reads themed and cohesive; transcript + all data stay fully legible; pillar identity (hue
    lines) preserved.
  - **Reverts cleanly to standard.** **Reduced-motion respected** (mic-signal breathe already honors reduce).
- **Verify (devloop):** force `#stage-live` visible, enter Halloween; screenshot dark + light; confirm
  transcript/costs readable.

## T8 — Control pillar jack-o'-lantern + replace the box-shadow flicker

- **Scope:** Escalate the Control pillar (home of the Voice-mode row) into the thematic centerpiece: carved-
  pumpkin inner glow + candle flicker. **Re-implement the flicker as `opacity` on an overlay pseudo-element**
  — this directly **replaces the reviewer-flagged `@keyframes hw-glow` box-shadow animation**
  (studio.css ~1001–1006). Escalate the `#voiceMode[data-mode="halloween"]` row to read "the spell is
  active" (chip, pumpkin→violet top-prob bar are already present — tie them into the centerpiece).
- **Files:** `app/web/public/studio.css`.
- **Acceptance criteria:**
  - Control pillar glows/flickers like a carved lantern; **no animated `box-shadow`** remains (grep the
    diff — the old `hw-glow` box-shadow keyframes are gone/replaced by an opacity overlay).
  - Flicker is GPU-cheap (opacity only); the Voice-mode row reads as the active-mode centerpiece and stays
    legible.
  - **Reverts cleanly to standard** (overlay returns to `opacity:0`; standard Control pillar unchanged).
  - **Reduced-motion respected:** flicker holds a static, finished frame under reduce (extend the existing
    reduced-motion override for the Control pillar).
- **Verify (devloop):** enter Halloween on Live; watch the flicker; emulate reduce → static; Performance
  trace shows no large per-frame paint from the pillar (contrast with the old box-shadow loop).

## T9 — Typographic treatment + lazy display font

- **T9a — Typography CSS *(studio.css, SEQUENTIAL)*:** add `--font-display` to `:root` (default
  `var(--font)`), re-declare it under Halloween to `"Cormorant", var(--font)`; apply `--font-display` to
  `.hero-title`, `.generate-title`, `.rail-head h2`, `.summary-head h3`; add the one-shot `@keyframes
  hw-settle` + static pumpkin heading glow + micro letter-spacing (per DESIGN.md §2). Reduced-motion:
  `hw-settle` → none.
- **T9b — Lazy font loader *(studio.js, PARALLEL)*:** on the **first** entry to Halloween (in/next to
  `applyVoiceMode` when `mode === "halloween"`), inject the Cormorant `<link rel="stylesheet">` once
  (guard against double-inject); reuse the existing `fonts.gstatic.com` preconnect. `font-display: swap`.
  No other behavior change.
- **Files:** `app/web/public/studio.css` (T9a), `app/web/public/studio.js` (T9b). *(Optionally a preconnect
  already exists in `index.html`; no new `<head>` font link.)*
- **Acceptance criteria:**
  - Display headings render in DM Sans immediately and **before/if** the serif loads (fallback proven by
    blocking the font request); serif swaps in on first Halloween entry only.
  - No font request on standard calls or `architecture.html` (lazy, gated on first Halloween entry).
  - Data/mono type is untouched; headings stay legible.
  - **Reverts cleanly to standard:** headings return to DM Sans styling; the injected `<link>` may remain
    cached (harmless, no visual residue). `hw-settle` does not replay on exit.
  - **Reduced-motion respected:** `hw-settle` disabled under reduce (headings appear statically).
- **Verify (devloop):** load studio (Network: no Cormorant request) → enter Halloween (one Cormorant
  request; headings swap) → exit → re-enter (no second request). Block the font in DevTools and confirm
  headings still legible in DM Sans. Emulate reduce → no settle animation.

## T10 — Accessibility & reduced-motion hardening (consolidation)

- **Scope:** Final theme-wide sweep of the existing `@media (prefers-reduced-motion: reduce)` block: ensure
  every Halloween animation added in T4/T8/T9a is listed/neutralized; confirm the static reduced frame is
  coherent; run the full contrast audit across both themed palettes and fix any token that misses AA; verify
  `aria-hidden`/`pointer-events:none` on all atmosphere; confirm `:focus-visible` ring contrast on themed
  surfaces.
- **Files:** `app/web/public/studio.css`.
- **Acceptance criteria:**
  - Under `prefers-reduced-motion: reduce`, **zero** theme animation runs; the mode still reads as themed
    (static) and fully legible.
  - Documented contrast audit: all themed text/accent pairs pass AA (dark + light).
  - Atmosphere never reaches the a11y tree or intercepts pointer/focus.
  - **Reverts cleanly to standard** (no new persistent state).
- **Verify (devloop):** reduced-motion emulation across all three stages, both themes; tab through the UI
  to confirm focus rings; run an a11y/contrast audit (e.g., Lighthouse) in themed state.

## T11 — Verbal bridge + ElevenLabs prewarm (agent-side)  *(FULLY PARALLEL — independent of all CSS)*

- **Scope:** Shrink/cover the silent gap when the voice switches. Two parts:
  1. **Verbal bridge:** emit a short, themed bridge line **in the current voice** at the moment a switch is
     committed, so there is no dead air while the new (ElevenLabs) voice comes online. On a same-turn enter,
     the agent currently opens the reply in the *new* voice (`ON_NOTE`), which must wait for the ElevenLabs
     connection; a bridge line (e.g. a brief "One moment…"-class cue) spoken in the *outgoing* voice covers
     that cold-start. Wire it through the switch path in `VoiceModeController._transition` / `on_turn`
     (where `on_cue`/the transition already run) and the prompt notes in `agent_prompt.py`
     (`ON_NOTE`/`OFF_NOTE`/`ANNOUNCE_*`/`VOICE_REQUESTS`) so the bridge and the post-switch flourish don't
     contradict each other. Must never raise (the turn-never-raises invariant).
  2. **Prewarm / keep-warm:** the controller already calls `on_cue=_prewarm_halloween` on the earliest cue
     (`app/agent.py` `_prewarm_halloween`; `VoiceModeController.on_turn`). Make the prewarm actually warm the
     ElevenLabs connection/WS (today it only calls a `prewarm()` if the TTS object exposes one) and keep the
     WS warm for the rest of the call so the first spooky utterance isn't slow. Keep it best-effort and
     outside the session's error counter (C10 — a vendor hiccup must not close the session).
- **Files:** `app/agent.py`, `src/agent_prompt.py`, `app/voice_mode.py`. *(No front-end files.)*
- **Acceptance criteria:**
  - On a voice switch, the caller hears a short themed bridge line in the **current** voice (no dead air);
    the subsequent reply/flourish lands in the new voice without a contradictory "One moment" (prompt notes
    reconciled).
  - The ElevenLabs connection is warmed on the earliest cue and kept warm; measurable reduction in the gap
    to the first Halloween utterance (log/trace the switch→first-audio latency before/after).
  - Prewarm and bridge are best-effort and **never raise**; a vendor failure still degrades to the fallback
    voice (existing `mark_degraded`), never closes the session.
  - `UG_AI_DECIDE=0` disables the whole path (no bridge, no prewarm) exactly as today.
- **Verify:** unit-test the controller path (bridge emitted once per switch; never raises on partial/failed
  classify; disabled when `enabled=False`); a scripted call (or the existing test harness) showing the
  bridge line + reduced switch→first-audio latency in logs/trace. (No browser needed.)

## T12 — Full devloop verification & performance pass  *(FINAL — after all)*

- **Scope:** End-to-end QA of the whole theme. No source changes (fix-forward by filing follow-ups or small
  patches to the owning task).
- **Files:** none (verification only).
- **Acceptance criteria / checklist:**
  - **Enter/exit** smooth in both dark and light; full enter→exit cycle leaves the studio
    **pixel-identical** to its pre-entry state (before/after screenshot diff) — the zero-residue guarantee.
  - **Reduced-motion:** no theme animation anywhere; static themed skin legible across all three stages,
    both themes.
  - **Contrast:** AA pass across themed text/accent/surfaces (dark + light).
  - **Performance:** a DevTools Performance trace during Halloween shows no long paints / no animated
    box-shadow; compositor layers released after exit; main thread not blocked by theme (voice pipeline
    unaffected).
  - **Non-breaking:** all four pillars and existing flows work in Halloween; `architecture.html` unchanged;
    standard calls unchanged.
  - **Agent-side (T11):** bridge audible + prewarm latency win confirmed in a scripted call.
- **Verify (devloop):** scripted enter→exit cycles via `evaluate_script`; screenshot diffs; Lighthouse +
  Performance traces; reduced-motion + light/dark matrix; confirm `architecture.html` and standard studio
  untouched.

---

## Notes for issue creation

- Label the `studio.css` tasks (**T0, T1, T2, T4, T5, T6, T7, T8, T9a, T10**) `sequential:studio.css` — one
  owner / ordered commits to avoid conflicts.
- Label **T3** (index.html), **T9b** (studio.js), **T11** (agent-side) `parallel` — disjoint files, safe to
  start immediately.
- **T4** depends on **T3** (DOM) + **T0** (tokens); **T5** depends on **T9a** for the display font (but
  degrades to DM Sans, so it can land first and gain the serif when T9 merges).
- **T12** is the merge gate.
