# Validrow — design notes

Running log of the UI/UX pass on the Streamlit front-end. Decisions, tokens, and
issues found but deliberately not fixed.

**Stack:** Streamlit 1.50.0 · `frontend/app.py` · `.streamlit/config.toml`
**Run:** `make ui` (needs `make run` for the API on :8000)

---

## Principles this pass is held to

1. **Precision is the product claim.** Validrow sells accuracy. A percentage that
   doesn't sum, a chart segment with no legend row, or three KPIs sharing one
   sparkline tells the user the system is careless with data. Alignment and
   rounding are trust signals here, not polish.
2. **The supported styling layer first.** Anything `[theme]` in
   `config.toml` can express lives there. CSS covers only the remainder, and
   targets `data-testid` / `data-baseweb` attributes — never generated class
   names like `.st-emotion-cache-1x2y3z`, which are hashed and change on upgrade.
3. **One definition, consumed everywhere.** If the same decision is expressed in
   two places, that's the bug — not the styling of either place.

---

## Phase 1 — the system

### Colour

The palette was locked to colours already present. Two things were true of the
existing set and had to be resolved before tokenising:

- **Two neutral ramps were live.** A cool one (`#878b95` `#ecedf1` `#f2f3f6`) and
  a **warm leftover from a previous "cream" design** (`#79756c` `#f0eee9`
  `#e7e4de` `#fffdf9`). Charts on the *same screen* disagreed: the Analytics area
  chart had cool axes, the donut and weekday bar had warm ones, and the donut's
  `#fffdf9` segment stroke left a visible seam against its `#ffffff` card.
  **Resolved:** the cool ramp is canonical; the warm one is deleted.
- **Two near-identical blues were live.** `BLUE = "#2e90fa"` in Python vs
  `--blue: #2e8fff` in CSS vs `primaryColor = "#2e8fff"` in config. Sparklines
  and score bars used one, buttons and nav the other. **Resolved:** one accent.

**Neutral ramp — 12 steps.** Existing neutrals kept; two steps (`#d3d6dd`,
`#5b5f68`) derived to fill gaps, which the brief permits for the grey ramp.
Contrast is measured, not estimated — worst case across `#ffffff`, `#f6f7f9` and
`#f1f2f5`:

| Token | Value | Role | Worst-case contrast |
|---|---|---|---|
| `--n0` | `#ffffff` | elevated surface | — |
| `--n50` | `#f6f7f9` | page canvas | — |
| `--n100` | `#f1f2f5` | hover / inset surface | — |
| `--n150` | `#ecedf1` | divider *inside* a component | — |
| `--n200` | `#e3e5ea` | border *around* a component | — |
| `--n300` | `#d3d6dd` | border, hover — *derived* | — |
| `--n400` | `#b0b4bd` | disabled text and marks only | 1.86 |
| `--n500` | `#878b95` | **decorative marks only, never text** | 3.05 |
| `--n600` | `#6c707a` | tertiary text, captions | **4.43** |
| `--n700` | `#5b5f68` | secondary text — *derived* | **5.72** |
| `--n800` | `#3a3c44` | body text | 9.82 |
| `--n900` | `#14151a` | headings, primary numerals | 16.29 |

Use the **named roles** (`--text-1..4`, `--border`, `--divider`, `--surface`,
`--surface-inset`, `--canvas`, `--mark`), not the ramp steps directly.

### Contrast failures fixed

| What | Was | Now |
|---|---|---|
| Table column headers, eyebrows | `#b0b4bd` — **2.08:1** | `--text-4` 4.43:1 |
| Captions, card footers, secondary meta | `#878b95` — **3.18:1** | `--text-4` 4.43:1 |
| Chart axis labels | `#878b95` / `#79756c` | `--text-4` 4.43:1 |
| **Primary button label** | white on `#2e8fff` — **3.24:1** | white on `#1560d0` **5.81:1** |
| Status dots | 2.35–3.76:1 | 4.96–5.78:1 |

`#1560d0` was already in the stylesheet as `--blue-dark`, so the accent fix
introduced no new hue.

### Status taxonomy — the central decision

The engine emits **six** status values; the brief calls for **four** primary
verdicts. Before this pass, `disposable` and `spam_trap` shared `invalid`'s exact
red — so colour already couldn't distinguish them — while the Analytics legend
listed only four of the six it charted.

**Resolved:** four primary verdicts, four muted colours, one shape (a small
filled dot plus a text label). `disposable` and `spam_trap` are treated as
*reasons* an address is undeliverable, not as verdicts: they resolve to the
Undeliverable colour and label, and surface their specificity as grey secondary
text beside it.

**This is a display mapping only.** `STATUS_ORDER`, the API payloads, the export
columns, `history_store`'s counts and every filter still see all six keys.

Colours: same hue as before, saturation pulled down, lightness to the middle.
One colour per status serves both dot and label, so each had to clear the
stricter 4.5:1 *text* threshold — which means every one also clears 3:1 as a
mark.

| Verdict | Token | Value | Hue | Contrast (white / canvas / inset) |
|---|---|---|---|---|
| Deliverable | `--st-ok` | `#16744a` | 153° (was 152°) | 5.78 / 5.40 / 5.17 |
| Risky | `--st-risk` | `#9a6410` | 37° (was 34°) | 4.99 / 4.66 / 4.46 |
| Undeliverable | `--st-bad` | `#c43c2f` | 5° (was 4°) | 5.21 / 4.86 / 4.65 |
| Unknown | `--st-unk` | `#6c707a` | neutral | 4.96 / 4.62 / 4.43 |

`Unknown` is deliberately the neutral ramp step rather than a hue: "no answer"
should read as absence, not as a fifth category.

**Known limitation:** all four sit at ~5:1, so they are distinguishable by *hue*
but not by *luminance* — in greyscale they are near-identical. This is acceptable
only because the text label carries the meaning and colour merely reinforces it,
per the brief. Pushing them to distinct luminances would drive Deliverable dark
enough to stop reading as green. **Never rely on the dot alone anywhere.**

### Other systems

- **Spacing:** 4px scale (`--s1`…`--s16` = 4/8/12/16/20/24/32/40/48/64). Every
  arbitrary value deleted; `rem`/`px` mixing gone.
- **Type:** 8 named styles, down from **16 distinct sizes and 14 weights**.
  Hierarchy comes from weight and colour first, size second — 4 weights only
  (400/550/620/640). `headingFontSizes` / `headingFontWeights` /
  `baseFontWeight` now set natively in config, so `st.markdown` headings and
  custom HTML agree.
- **Tabular numerals ON globally.** `:root` previously set
  `font-feature-settings: 'tnum' 0` — disabling tabular figures — then re-enabled
  them per-class in six places, leaving captions, chart axes and dataframes with
  proportional digits. For a product selling numeric precision this was exactly
  backwards.
- **Radii:** 3 values (`--r-sm` 6px inputs/buttons, `--r-md` 10px cards,
  `--r-pill`), down from 8. `baseRadius`/`buttonRadius` set in config to match.
- **Elevation:** 3 levels, border-led (`--e0` border only, `--e1` resting card,
  `--e2` overlay), down from 5 named + ~8 bespoke inline glows.
- **Motion:** one duration set (120/200/300ms) and one curve, down from 8
  durations. `prefers-reduced-motion` also now caps `animation-iteration-count`,
  which infinite animations previously escaped.
- **Buttons:** one height app-wide (34px) across `.chip-btn`, `.btn-primary` and
  native `stButton`, which had no shared height and so never lined up.
- **Labels:** `.eyebrow` is now the single definition of "small uppercase label",
  replacing four (`.side-label`, `.stat .l`, `.vt-head span`, `.fact .l`) that
  disagreed on both size and weight.

### Removed

- The page-background radial gradient washes; **20 gradients** in total, down to 0.
- The gradient hero KPI card, plus its glow shadow (see *Decisions* below).
- The glowing `box-shadow` on the active nav indicator.
- The `rise` entry animation on every top-level block. Streamlit re-runs the
  whole script on every interaction, so this re-animated the entire page on every
  click — and once per second during job polling. Decorative animation on
  content, and the single biggest source of jank.
- The 8-hue `_avatar_color` palette (`#7a5af8` purple, `#ec4899` pink,
  `#06aed4` cyan, …). None existed in the theme, and it rendered as dots directly
  beside status dots — a second dot system carrying no meaning. Now neutral ramp
  steps.
- The `✅` emoji favicon, replaced with an inline SVG from the app's own icon set.
- Dead code: `_axis_font()` (a no-op), `badge()` (an alias), `_grad_seq`.

### New guarantees

- `fmt_pct(part, whole)` — one decimal place, always. `—` when the denominator is
  zero, rather than a divide-by-zero or a misleading `0.0%`.
- `pct_parts(values)` — largest-remainder rounding, so a set of shares **sums to
  exactly 100%**.
- `verdict_totals(counts)` — the single collapse from six statuses to four
  verdicts. Every chart and legend consumes it, so a segment can no longer exist
  without a matching legend row.
- `sparkline()` returns `""` for a series with fewer than two real points instead
  of inventing a placeholder curve, and `stat_card` no longer reserves the box
  when there's nothing to draw.
- `stat_card(delta=…)` is now only ever a **change**; `delta_up=None` gives a
  neutral style, so nothing can show a green up-arrow it hasn't earned.

---

## Decisions where I picked

**Empty states stay centred.** The brief forbids centred body text. A
single-sentence empty state is the conventional exception across this category
(and in Linear and Notion), and left-aligning it inside a wide card reads as
broken rather than deliberate. Centring is confined to `.empty` and to marks
(badges, step numbers, avatars) — verified by static audit. Everything that is
genuinely body text, and every form label and table cell, is left-aligned.

**Two status tokens are declared but unused in CSS.** `--st-unk` and
`--st-unk-soft` have no CSS consumer, because nothing in the stylesheet renders
an "unknown" state — the status colours reach the DOM from Python. They stay
anyway: an incomplete set of four is what makes the next person hardcode the
missing one.

**Hero KPI card → neutralised.** The Dashboard's lead KPI was a saturated blue
gradient block with a glow shadow — the most eye-catching thing on the screen and
also squarely on the brief's "never" list. Options were (A) neutralise to a
bordered surface matching its siblings, carrying emphasis through type weight and
scale, or (B) keep one accent-filled card as the deliberate single accent, flat
fill, no glow. **Took A**: the KPI row now reads as one system, and the accent is
freed for the single primary action per screen.

**Status colours: hue preserved, saturation cut.** The brief asks to desaturate
*and* forbids new hues. Read as: same hue, lower saturation, mid lightness. A
first pass at ~18% saturation was rejected — `#537766` / `#7d6d57` / `#89625f`
read as olive, brown and mauve, losing the hue's reinforcing role entirely. Final
values sit at 61–81% saturation with lightness pulled to 27–48%, which keeps them
legible as green/amber/red while staying calm against the neutral ramp.

---

## Phase 2 — components

**The results table moved to native `st.dataframe` + `column_config`.** It was a
hand-built HTML grid capped at the first 60 rows (200 on Contacts) with a caption
explaining the truncation. Native gives virtualisation, a sticky header,
resizable columns and per-column alignment; `pinned=True` freezes the address
column, which is the small-screen strategy for the table.

Status colour reaches the cells through a `Styler`, driven by the same
`status_color()` the cards and charts use. The dot is **U+25CF**, a geometric
shape rather than an emoji, so it renders monochrome in the cell's own colour and
the indicator looks identical in the table and in custom HTML.

Two hand-built list views stayed as HTML — the job list and the finished-jobs
list — because their rows are whole-row links into a detail route and their row
counts are in the dozens. They were hardened rather than replaced: sticky header,
fixed row height, ellipsis truncation, right-aligned numerals, and a sticky first
column under 640px.

**A bug the 50k test caught:** pandas refuses to style a frame over 262,144
cells, and Contacts (7 columns × 50,008 rows) exceeded it — Streamlit surfaced a
raw exception. Styling also costs ~0.66s per rerun at 350k cells, and Streamlit
reruns the whole script on every keystroke in the filter box. `styled_table()`
now raises the pandas ceiling and applies its own `STYLE_CELL_BUDGET`; above it,
colour is dropped but the dot and label remain. That degradation is only
acceptable because the label carries the meaning — the same property that makes
the table readable with a red/green colour vision deficiency. Contacts also lost
its redundant Domain column (the address beside it already shows the domain),
which brings 50k rows back inside the budget.

Also in this phase: empty-after-filter distinguished from empty-no-data; a `bare`
empty state so cards stop nesting inside cards; a destructive-confirm popover
that names the specific file and puts the consequence in the button label;
dropzone idle/hover/focus states; a disabled button state; icons down to four
optical sizes; and one primary action per screen.

## Phase 3 — screens

Every trust defect from the audit, all of which were visible in the running app:

| Defect | Fix |
|---|---|
| Analytics legend listed 4 statuses while the donut charted 6 — two same-coloured red segments had no legend row, and shares summed to **87.5%** | Both iterate `verdict_totals`; shares from `pct_parts` |
| A green "↗ {rate} pts" delta that was the rate itself relabelled, against no comparison period | Replaced with the rate and the counts behind it |
| Three Dashboard KPIs drew the **same sparkline** — "Avg. quality score" was plotting volume | Rate card plots rate-per-day; score card shows the verdict mix |
| Share-of-total rendered as a trend delta, including a red "↗ 37.5%" where up is bad | Shares moved to the card footer |
| Mixed precision for one metric across screens (82.4% here, 82% there) | Everything goes through `fmt_pct` |
| Short series produced ticks that all formatted identically ("Jul 18 Jul 18 Jul 19 Jul 19") | One tick per day |
| Tables left blank rows underneath, because the container height was computed from a guessed row height | `row_height` pinned; `df_height()` is exact |

Screens: the Dashboard leads with running jobs (most people land there to check
on one, and it sat below three KPI tiles). Job progress gained real phases —
queued, reading, resolving, verifying, finalizing — partial verdict counts as
they land, a fixed-width progress track so reaching 100% can't collapse the
layout, and an explicit "you can leave this page". A failed job now shows what
completed before the failure. `job_phase()` is shared by the dashboard row and
the job screen so the two cannot report different progress. Bulk upload shows the
parsed rows *before* any column choice.

**Vocabulary, one word per concept:** job (not run/upload/batch), addresses (not
contacts/mailboxes), Deliverable (not Valid) — applied to labels, empty states,
error messages and export filenames.

## Precision rules

Beyond "one decimal place", two guards were added after edge-case testing found
10,000 deliverable against 3 undeliverable rendering as **"100.0%"** with three
**"0.0%"** rows:

- `fmt_pct` shows `<0.1%` for a non-zero part that rounds to zero, and `>99.9%`
  for a part short of the whole that rounds to 100.
- `pct_parts` gives every non-zero category its smallest visible unit, taken from
  the largest share, so no category shows 0.0% with a real count behind it and no
  single share shows 100.0% while another is non-zero. The total still comes to
  exactly 100%.

Rounding that claims an absolute it hasn't reached is exactly the kind of small
lie this product cannot afford.

## Verification

- **50,000-row result set** — renders; a 60-character address truncates with an
  ellipsis rather than wrapping and changing the row height.
- **Edge cases** — empty account, all-undeliverable list, failed job, zero
  denominators, and four rounding cases that would otherwise not sum to 100.
- **Contrast** — every text and status token measured against `#ffffff`,
  `#f6f7f9` and `#f1f2f5`; worst case 4.43:1.
- **Static audit** — 0 gradients, 0 raw hex outside `:root`, no hashed class
  selectors, no `outline:none`, no blur, centring confined to empty states and
  marks, tabular numerals on globally. 78 tokens declared, 76 referenced.
- **Keyboard** — tabbing reaches sidebar nav with a visible 2px focus ring.
- **All nine routes** boot with no server exception and no console error.

### Not verified

- **Narrow viewports were not visually confirmed.** The browser tooling here
  resized the OS window without changing the page viewport (`innerWidth` stayed
  1920), so the `640px` / `380px` media queries never fired. As a substitute, the
  layout was probed by forcing the content container to 320px and measuring
  overflow: every hand-built element fits, and the only thing exceeding it is
  `st.dataframe`'s canvas, which measures its container at render time rather
  than reflowing, so the probe can't judge it. **Worth a manual check at 320 /
  360 / 768 before shipping.**
- Dark mode — none exists to check (`base = "light"` only).

## Found but NOT fixed — needs a product decision

Behavioural or data-integrity issues, outside a visual pass.

1. **Credits are referenced but never shown.** Single-check copy says "~1 credit
   per check", but no balance, quota or cost confirmation exists anywhere. Bulk
   upload spends credits with no confirmation step. The brief expects the balance
   findable in under a second.
2. **Missing screens:** API keys & docs, billing / credits / plans, and team /
   roles / webhooks. Settings covers only engine connection and health. (The
   copy-to-clipboard and key-revoke components the brief asks for have nowhere to
   live until the API keys screen exists.)
3. **Job polling re-runs the whole script every second** (`time.sleep(1.0);
   st.rerun()`). The visible symptoms are fixed — the page no longer re-animates,
   and progress is determinate — but the mechanism is still a full rerun per
   second. A fragment (`@st.fragment(run_every=…)`) would scope it to the
   progress block. That's a behavioural change, so it was left alone.
4. **Contacts still has no pagination**, only virtualised scroll. Fine to 50k;
   an account with millions of addresses needs server-side paging, which needs an
   API change.
5. **`load_contacts` reads and concatenates every cached CSV** on a cache miss.
   Cached by `store_signature()`, so it's rare, but it is O(all data) in memory.
6. **No dark theme.** `[theme]` supports it and the token layer is ready
   (every colour is a named role), but it is new surface area and needs its own
   contrast pass.

### Fixed along the way

These were on the audit's "not fixed" list and turned out to be display-layer
problems after all: the fabricated **Name** column on Contacts (it derived a
person's name from the email local part, producing "Throwaway" and
"Johndoe+Newsletter" as if they were real names — the column is gone); the
duplicate **Home** nav entry; the **fake search box** with its unbound ⌘K hint;
the **hardcoded user block** (now real engine status); the sidebar's
`calc(100vh - 640px)` magic number (now a flex spacer); the missing job states;
and the 60/200-row table caps.

---

## Vocabulary — pick one word per concept

Currently interchangeable, needs standardising in copy, error messages and the
API docs:

| Concept | Words in use | Proposed |
|---|---|---|
| one bulk validation | run, job, validation, upload, list, batch | **job** |
| the file's rows | contacts, addresses, mailboxes, emails | **addresses** |
| the good verdict | Valid, Deliverable | **Deliverable** |
| unit of spend | credits, validations | **credits** |
