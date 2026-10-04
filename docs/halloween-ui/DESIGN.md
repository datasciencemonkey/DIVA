# Halloween UI — Full-Theme Design

**Status:** design spec (build from this; do not treat as final CSS).
**Scope:** escalate the restrained `data-voice-mode="halloween"` accent into a cohesive, tasteful,
full-studio seasonal skin that reverts perfectly to the normal studio on exit.
**Codename:** *All Hallows' Console* — mission control at midnight on Halloween. Crafted, not cartoon.

---

## 0. The one idea that makes this cheap, cohesive, and reversible

Every surface, text, accent, and motif in `studio.css` is already drawn from CSS **custom properties**
(`--ink`, `--surface`, `--accent`, `--text-hi`, the four pillar hues, `--hairline`, …). Light theme is
already implemented this way: `:root[data-theme="light"]` just *re-declares the tokens* and the whole
studio follows.

**We do the same thing for Halloween.** A single scoped block —

```css
body[data-voice-mode="halloween"] { /* re-declare the palette tokens */ }
```

— retints roughly **70% of the studio in one cascade**: body, cards, pillars, inputs, buttons, the Lava
CTA, the connected chip, transcript bubbles, the hero motif strokes, the cost meter, scrollbars. No
per-component rewrites for the base palette. The remaining 30% is *atmosphere* (fog, moon, embers, bats,
jack-o'-lantern glow), *typographic treatment* (a display face on headings), and *choreography*
(enter/exit cross-fade), layered additively on top.

**Why it reverts with zero residue:** the theme is 100% attribute-driven CSS. `applyVoiceMode("standard")`
/ `resetVoiceMode()` already remove `body[data-voice-mode]`. The moment the attribute leaves, every
`body[data-voice-mode="halloween"]{…}` rule stops matching, tokens snap back to `:root`, atmosphere fades
to `opacity:0`, and all keyframe loops (defined only under that selector) stop. No JS writes inline styles
for the theme, so there is nothing to clean up. (See §7 for the explicit revert guarantees.)

**Scoping guard — `architecture.html` shares `studio.css`.** `architecture.html` links the same stylesheet
but its `<body>` never carries `data-voice-mode`. Every rule in this spec MUST be scoped under
`body[data-voice-mode="halloween"]` (or a `.hw-atmos` element that only exists on the studio page) so the
architecture page and every standard call are byte-for-byte unchanged.

---

## 1. Palette

Two themed palettes — one for the default dark studio, one for the light studio — because the user can be
in either theme when the agent flips the voice. Both are **token re-declarations**, so they compose with
`data-theme` automatically.

Reuse the three tokens already in the file (`--hw-orange`, `--hw-violet`, `--hw-ink`, lines ~910–919) as
the signature hues; add the full surface/text ramp.

### 1.1 Dark Halloween — "Midnight" (`body[data-voice-mode="halloween"]`, default theme)

A haunted indigo-black with a violet undertone and a pumpkin signal. Pumpkin **replaces Lava** as `--accent`.

| Token | Hex | Role | Contrast check |
|---|---|---|---|
| `--ink` | `#0C0718` | page / `.bg` | — |
| `--ink-2` | `#140C24` | insets, inputs, card-gradient base | — |
| `--surface` | `#1A1230` | card top | — |
| `--surface-2` | `#221834` | resting controls, bubbles | — |
| `--surface-3` | `#2C2144` | hover / pressed | — |
| `--hairline` | `rgba(157,107,255,.16)` | violet hairline | — |
| `--hairline-strong` | `rgba(157,107,255,.30)` | strong hairline | — |
| `--topbar-bg` | `linear-gradient(to bottom, rgba(12,7,24,.86), rgba(12,7,24,.58))` | sticky topbar | — |
| `--text-hi` | `#F3ECFA` | primary text | ~14:1 on `--surface` ✅ |
| `--text-mid` | `#C6B7E0` | secondary text | ≥6:1 on all themed surfaces ✅ |
| `--text-lo` | `#AD9ECB` | tertiary / labels | ≥4.6:1 on all themed surfaces ✅ |
| `--accent` | `#FF8A2B` (`--hw-orange`) | CTA / focus / connected / "live" / hero fills | — |
| `--accent-deep` | `#F0761A` | primary-button gradient base | — |
| `--accent-ink` | `#1A0C02` | label on the pumpkin button | ≥8:1 on both stops ✅ |
| `--accent-text` | `#FFB877` (`--hw-ink`) | accent-as-TEXT | ≥6:1 on themed navy-violet ✅ |
| `--c-choice` | `#A6ABEA` | pillar: Choice (unchanged) | keeps data identity ✅ |
| `--c-control` | `#FFA600` | pillar: Control (unchanged — amber already reads "Halloween") | ✅ |
| `--c-context` | `#99DDB4` | pillar: Context (unchanged) | ✅ |
| `--c-costs` | `#D493CE` | pillar: Costs (unchanged — violet-pink harmonizes) | ✅ |
| `--danger` | `#FF6E78` | functional (unchanged) | ✅ |
| `--shadow` | `0 26px 64px -24px rgba(0,0,0,.82)` | card shadow (deeper, violet-cast rim added per-component where useful) | — |

**Keep the four pillar hues unchanged.** They carry *data meaning* (which pillar is which); recoloring
them to "all orange" would both reduce legibility and read as a costume. The theme reads loud and clear
through surfaces + accent + atmosphere; the pillars stay trustworthy.

### 1.2 Light Halloween — "Harvest" (`:root[data-theme="light"] body[data-voice-mode="halloween"]`)

Warm parchment + aubergine ink + a darkened pumpkin signal. Mirrors the existing light-theme discipline
(darkened hues for AA on a pale ground).

| Token | Hex | Role | Contrast check |
|---|---|---|---|
| `--ink` | `#F6EEE2` | parchment page | — |
| `--ink-2` | `#EADFCB` | insets, inputs | — |
| `--surface` | `#FFFBF3` | card top | — |
| `--surface-2` | `#F3E9D7` | resting controls | — |
| `--surface-3` | `#E7D8BF` | hover / pressed | — |
| `--hairline` | `rgba(107,63,209,.16)` | violet hairline | — |
| `--hairline-strong` | `rgba(107,63,209,.28)` | strong hairline | — |
| `--text-hi` | `#241433` | deep aubergine | ~13:1 on parchment ✅ |
| `--text-mid` | `#4C3A52` | secondary | ≥7:1 on all light surfaces ✅ |
| `--text-lo` | `#5E4A64` | tertiary | ≥5:1 on all light surfaces ✅ |
| `--accent` | `#C2560A` (`--hw-orange` light) | CTA / focus / connected | white label 5.8:1 ✅ |
| `--accent-deep` | `#A8470A` | button gradient base | ✅ |
| `--accent-ink` | `#FFFFFF` | label on the pumpkin button | ≥5:1 ✅ |
| `--accent-text` | `#9A3F06` | accent-as-TEXT on parchment | AA ✅ |
| pillar hues | *(keep the existing light values)* | data identity | ✅ |

> **Build note:** declare the dark palette on `body[data-voice-mode="halloween"]`, then the light palette
> on `:root[data-theme="light"] body[data-voice-mode="halloween"]`. Order after the existing light block so
> specificity resolves correctly. Verify each `--text-*` and `--accent-text` against `--surface`,
> `--surface-3`, and `--ink-2` with a contrast tool before merge (the table values are targets).

---

## 2. Typography

**Body & every data readout stay DM Sans / the mono stack — untouched.** Legibility is non-negotiable: the
agent states real facts (G13), and transcript, token counts, directives, retrieval queries and prices must
read exactly as crisply as in standard. No theming of data type.

**One display face, on the big headings only, as a progressive enhancement.** This is the single biggest
"crafted seasonal skin" lever and the one place a themed face earns its keep.

- **Token:** add `--font-display` to `:root`, defaulting to the existing display stack
  (`var(--font)` — DM Sans). Under Halloween, re-declare it to the themed face with a DM Sans fallback:
  `--font-display: "Cormorant", var(--font);`
- **Face:** **Cormorant** (Google Fonts, variable, elegant high-contrast serif). It reads *refined and
  slightly gothic* at display size — tasteful, never a "spooky font." Loaded weight 600–700, italic
  available for the hero's `<em>`. (Alternative if a heavier mood is wanted: **"Cormorant Garamond"**; avoid
  blackletter/"Creepster"-class faces — they fail the legibility + taste bar.)
- **Applied to:** `.hero-title`, `.generate-title`, `.rail-head h2` (live "company" title), and the
  `.summary-head h3`. **Not** applied to pillar `h3`, labels, chips, or any mono readout — those stay
  DM Sans so the console still reads like an instrument.
- **Treatment under Halloween:** on the themed display headings, add a faint pumpkin text-glow
  `text-shadow: 0 0 18px color-mix(in srgb, var(--hw-orange) 30%, transparent)` (static, not animated),
  a hair more letter-spacing on the uppercase micro-labels, and keep the hero `<em>` Coral→pumpkin via the
  `--accent-text` swap (free).
- **Loading (perf + offline-safe):** do **not** add the font to the global `<head>` (that would cost a
  download on every studio visit, including standard calls, and on `architecture.html`). Instead
  **lazy-inject** the stylesheet `<link>` the first time Halloween is entered (see §5 / Task). Because
  `--font-display` falls back to DM Sans, headings are fully styled and legible *before* and *if* the font
  never loads; the serif swaps in with `font-display: swap` when ready. Reuse the existing
  `fonts.googleapis.com` / `fonts.gstatic.com` preconnects already in `index.html`.

---

## 3. Atmospheric layer

All atmosphere is **decorative, `aria-hidden`, `pointer-events:none`**, built from CSS gradients + a few
tiny inline SVGs, and animated with **transform/opacity only**. It lives in one new container,
`.hw-atmos`, added to `index.html` (a single additive block). In standard it is `display:none` (zero cost,
zero paint, no promoted layers); it is revealed only under `body[data-voice-mode="halloween"] .hw-atmos`.

Layer stack (back → front), all behind content (`z-index` below `.topbar`/`main`, like `.bg`):

1. **Nightfall tint (reuse `.bg::before`).** The existing wash pseudo-element already fades
   `opacity 0→1` on Halloween — escalate its gradient from the current faint 2-radial to a richer scene:
   a low pumpkin "lantern" glow rising from bottom-left, a spectral-violet pool top-right, and a subtle
   overall darken. Pure gradient; already `transition: opacity var(--dur-3)`. (Keeps its reduced-motion
   handling.)
2. **Moon.** A single CSS radial-gradient disc, top-right, soft pale-gold with a faint violet halo. Static
   (no animation). ~160–200px. Pure CSS — no asset.
3. **Fog bands.** Two wide, heavily-blurred horizontal gradient bands (`.hw-fog`) drifting slowly sideways
   via `transform: translateX()` on a long, de-synced loop (same "coprime durations never resync" trick the
   aurora uses). `will-change: transform` scoped under Halloween only. Opacity ~0.5. Blur is **static**
   (set once), never animated.
4. **Embers / spores.** 6–8 small dots (`.hw-ember`), pumpkin and violet, drifting upward and fading via
   `transform: translateY()` + `opacity` keyframes, staggered. Tiny, soft, slow — "slight but alive," the
   same restraint as the existing aurora. Pure CSS spans.
5. **Bats.** 2–3 small inline-SVG bat silhouettes (`.hw-bat`), one authored path reused, drifting across far
   back at low opacity with a gentle `translate` + micro `rotate` wing-tilt (transform only). Deliberately
   sparse and distant — a glimpse, not a swarm.
6. **Cobweb corners.** One authored inline-SVG web, hairline stroke in `--hairline-strong`, placed in the
   top-left (and optionally top-right, flipped) corner of the viewport frame at low opacity. Static. Adds
   "crafted" texture without noise.

**Jack-o'-lantern glow — the Control pillar.** The Control pillar hosts the "Voice mode · AI Decide" row,
so it is the thematic heart. Escalate today's restrained violet border/box-shadow into a carved-pumpkin
inner glow: a warm pumpkin radial behind the pillar body + a candle **flicker**. The flicker MUST be done
with **opacity on an overlay pseudo-element** (`.pillar[data-pillar="control"]::before/after` layer),
**not** by animating `box-shadow` — see §6 (this directly replaces the reviewer-flagged `@keyframes hw-glow`
box-shadow loop).

**Density discipline (so it stays 10/10, not a haunted-house gimmick):** moon ×1, fog ×2, embers ≤8, bats
≤3, cobweb ×1–2. If in doubt, fewer. The scene should whisper.

---

## 4. Motion & transition choreography

The signature stays the house ease (`--ease-signature`, `cubic-bezier(0.16,1,0.3,1)`) and the timing ladder
(`--dur-1/2/3`). Everything below is transform/opacity or color/background transitions — no layout, no
animated shadow/filter.

### 4.1 Enter (standard → halloween)

Trigger: `applyVoiceMode("halloween")` sets the attribute (already wired). Then, purely in CSS:

| Time | What happens | How |
|---|---|---|
| 0–420ms | Whole studio cross-fades navy→midnight-violet; Lava→pumpkin | token swap + `transition: background-color/border-color/color var(--dur-3)` added to major surfaces (Task: cross-fade) |
| 0–600ms | `.hw-atmos` + `.bg::before` wash fade in (moon, fog, embers, bats, cobweb appear) | `opacity 0→1`, `transition: opacity var(--dur-3)` |
| 120–900ms | Themed display headings "settle" (tiny fade/translate-up, one-shot) | one-shot `@keyframes hw-settle` attached under the Halloween selector — plays once on enter, never on exit |
| continuous | Fog drifts, embers rise, bats glide, lantern flickers | long de-synced transform/opacity loops, defined only under the Halloween selector |

The cross-fade transitions are **always declared** (not only under Halloween), so they fire symmetrically on
enter *and* exit; because the token values only change when the attribute toggles, they never fire during a
standard session. (Bonus: this also smooths the ordinary light/dark toggle — acceptable, arguably an
improvement; verify it introduces no flash on first paint, which it won't since initial load sets tokens
once with no transition.)

### 4.2 Exit (halloween → standard)

Trigger: `applyVoiceMode("standard")` / `resetVoiceMode()` removes the attribute. No new JS. CSS does it all:
every Halloween rule detaches → tokens revert (animated by the same cross-fade transitions) → `.hw-atmos`
and `.bg::before` fade to `opacity:0` → every keyframe loop stops (its selector no longer matches). The
one-shot heading settle does **not** replay. Net effect: a smooth ~420ms "sunrise" back to the normal
studio, no residue.

### 4.3 Reduced motion (`@media (prefers-reduced-motion: reduce)`)

Extend the file's existing reduced-motion block. Under reduce:

- **No animation anywhere in the theme:** fog drift, ember rise, bat glide, lantern flicker, and the
  heading settle are all `animation: none`. (The blanket `* { transition-duration:.001ms }` rule already
  neutralizes the cross-fade, so enter/exit become an instant, legible swap.)
- **Atmosphere holds a static, finished frame:** the scene is still *present* (moon, static fog, dim
  cobweb) so the mode still reads as themed — it simply does not move. Prefer hiding the drifting embers
  and bats entirely under reduce (movement with no state to preserve), matching how the file already
  removes the decorative `.hm-flow` signal packet under reduce.
- **No promoted layers left spinning:** `will-change` is scoped so it is inert under reduce.

Everything stays fully legible and contrast-complete with zero motion.

---

## 5. Element-by-element map

What each region becomes under `body[data-voice-mode="halloween"]`. Items marked **(free)** are achieved by
the §1 token swap alone — no extra rules needed.

- **`.bg` / ambient** — aurora orbs re-read as violet/pumpkin depth **(free, via token-driven gradients
  where applicable)**; `.bg::before` wash escalated (§3.1); `.hw-atmos` scene layered in (§3).
- **Topbar** — violet underline + pumpkin glow (escalate today's rule); `.brand-mark` pumpkin **(already
  in file)**; `.brand-word` faint pumpkin glow; `conn-chip[connected]` pumpkin dot **(free)**;
  theme-toggle themed **(free)**.
- **Stepper** — current step pumpkin tint **(free)**; done-check keeps `--c-context` green (reads as an
  eerie glow) or optionally pumpkin.
- **Hero** — display-serif title with static pumpkin glow (§2); `.em-line` underline pumpkin **(free)**;
  `.hero-motif` strokes retint automatically — spoken wave + chosen route + chosen node go pumpkin via
  `--accent`, the governance shield stays amber (`--c-control`) reading as a carved glow **(free)**;
  `.pillars-strip` cards take violet surfaces **(free)**.
- **Config card + form** — violet card + inset inputs **(free)**; focus ring pumpkin **(free)**; primary
  CTA pumpkin gradient **(free)**; ghost/preset chips themed **(free)**; `.btn-draft` pumpkin **(free)**.
- **Generate stage** — timeline connector gradient pumpkin→violet; active node pumpkin ring, done node
  pumpkin fill **(mostly free; a couple of accent rules reference `--accent`)**; summary/fail cards themed
  **(free)**; stat/tier readouts stay DM Sans/mono.
- **Live — rail / call-card** — violet card **(free)**; caller avatars + checks themed **(free)**;
  `.mic-signal` arcs + `.mic-meter` bars pumpkin **(free)**; connect CTA pumpkin **(free)**; `.ask-chip`
  grounded prompts themed **(free)**.
- **Live — pillars** — all four get violet surfaces + keep their top hue line **(free)**; **Control pillar**
  escalated to the jack-o'-lantern centerpiece (§3, §6); the **Voice-mode row** (`#voiceMode[data-mode=
  "halloween"]`) already themed — escalate to read as "the spell is active" (its chip, the pumpkin→violet
  top-prob bar are already in the file); Costs meter + sparkline retint via `--c-costs` **(free)**.
- **Live — transcript** — agent bubble tinted pumpkin, caller bubble violet, live dot pumpkin **(free, via
  `--accent`/`--c-choice`)**; transcript text stays DM Sans, fully legible.
- **Scrollbars** — themed via `--hairline-strong` **(free)**.

---

## 6. Performance notes

- **Transform/opacity only** for every animation (fog = `translateX`, embers = `translateY`+`opacity`,
  bats = `translate`+`rotate`, lantern flicker = `opacity`). No animated `width/height/top/left`, no
  layout thrash.
- **Replace the flagged `@keyframes hw-glow` box-shadow loop.** The current Halloween accent animates
  `box-shadow` on the Control pillar (studio.css ~1001–1006) — a reviewer flagged this; animated box-shadow
  repaints a large blurred region every frame. Re-implement the glow/flicker as an **opacity animation on a
  pseudo-element overlay** (a pre-rendered radial-gradient layer whose `opacity` eases between ~0.5 and
  ~0.85). Same look, GPU-cheap, no per-frame shadow repaint.
- **Blur is static.** Fog/moon blur is set once; never animate `filter`/`backdrop-filter`.
- **Atmosphere costs nothing in standard.** `.hw-atmos { display:none }` outside Halloween → no paint, no
  compositor layers. Scope `will-change: transform` *under the Halloween selector only* so no layer is
  promoted during standard calls.
- **No JS rAF loop for the theme.** All motion is CSS. (The only JS is the one-time lazy font-link
  injection.) This keeps the voice pipeline's main thread clear.
- **Lazy font.** The display face downloads only on first Halloween entry, `font-display: swap`, with the
  DM Sans fallback already rendering. No render-blocking, no cost on standard/architecture pages.
- **Keep loops de-synced + slow** (17/23/29s-class), matching the existing aurora, so the GPU stays idle
  between frames and nothing strobes.

---

## 7. Accessibility notes

- **Contrast:** every themed `--text-*` and `--accent-text` targets WCAG AA on its surfaces (≥4.5:1 body,
  ≥3:1 large) in **both** dark-Halloween and light-Halloween (§1 tables). Verify with a tool before merge.
- **Reduced motion:** fully honored (§4.3) — no animation under `prefers-reduced-motion: reduce`; the theme
  degrades to a static, legible skin.
- **Decorative-only atmosphere:** `.hw-atmos` and every motif are `aria-hidden="true"` and
  `pointer-events:none`; they never receive focus, never alter the accessibility tree, never cover content.
- **Not color-alone:** mode state is still carried by the text label in the `vm-chip` ("Halloween") and the
  voice-mode row copy — not by color alone.
- **Focus visibility preserved:** the keyboard `:focus-visible` ring stays (pumpkin `--accent`) with
  adequate contrast on themed surfaces.
- **Content legibility (G13):** data type (transcript, tokens, prices, directives, retrieval) is never
  restyled; the display serif is confined to non-data headings.
- **Respect the user's theme:** Halloween composes with `data-theme` (dark *or* light); we never force a
  theme flip on the user.

---

## 8. Revert guarantees (zero residue — the hard constraint)

- The theme is **100% attribute-driven CSS**. Removing `body[data-voice-mode="halloween"]` reverts
  everything. `resetVoiceMode()` / `applyVoiceMode("standard")` already do this on call end, "← New world,"
  and dropped connections.
- **No theme JS writes inline styles** → nothing to unwind. (Contrast: the mic meter sets inline styles, but
  that is existing, non-theme behavior and is already reset by `stopMicMeter`.)
- **One-shot animations** (heading settle) are defined under the Halloween selector, so they only play on
  enter and cannot leave a half-applied transform (they `clearProps`/complete, and the selector detaches on
  exit).
- **`.hw-atmos`** returns to `display:none` / `opacity:0` on exit; its loops stop because their selectors no
  longer match.
- **Lazy font `<link>`** remains cached in `<head>` after first use — harmless, invisible in standard
  (nothing references `--font-display`'s themed value outside Halloween). This is the only persistent
  artifact and it has no visual effect.
- **Verification:** diff the computed styles / screenshot of the studio *before* first Halloween entry and
  *after* a full enter→exit cycle — they must be identical (see TASKS.md final QA).

---

## 9. Assets

- **No raster images.** Moon, fog, embers, nightfall, lantern glow, vignette = CSS gradients.
- **Inline SVG motifs** (authored directly in `index.html` inside `.hw-atmos`, colored via `currentColor`/
  tokens, each a few hundred bytes): **bat** (one path, reused ×2–3), **cobweb corner** (one path, ×1–2).
  Optionally a small **carved-pumpkin** glyph for the Control-pillar / voice-mode centerpiece.
- **One web font (optional, lazy):** Cormorant (variable), Google Fonts, loaded on first Halloween entry
  with a DM Sans fallback. ~1 request, non-blocking.
- **SFX (out of scope here; later task):** if a one-shot "enter" chime is ever added, it must be a tiny
  (<30KB) asset, played only on explicit entry, muted under a reduced-motion/`prefers-reduced-motion`
  sensibility and behind a user-gesture (autoplay policy). Listed for completeness; not part of the visual
  build.

---

## 10. Non-goals

- No change to the data flow, the four pillars' logic, the evidence contract, or any server plumbing.
- No recoloring of pillar hues (data identity) or restyling of data/mono type (legibility).
- No new theme JS beyond the one-time lazy font-link injection.
- No forced theme flip; no reduced-motion violations; no animated box-shadow/filter/layout.
