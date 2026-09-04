# Validrow — design notes

Running log of the UI/UX work. Decisions, tokens, and issues found but
deliberately not fixed.

**Stack:** FastAPI + Jinja, server-rendered · `src/eve/web/`
**Run:** `make run` — the engine serves the API and the UI on :8000

> Phases 1–4 document the Streamlit front-end that preceded this and has since
> been deleted. They are kept because the *reasoning* still governs the product
> — the taxonomy, the precision rules and the contrast method all carried over.
> Anything referring to `frontend/app.py`, `.streamlit/config.toml`, `make ui` or
> a Streamlit API is historical. Phase 5 describes what actually runs.

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

---

## Phase 4 — the reference pass

> **Historical.** Phases 1–4 describe the Streamlit front-end, which was replaced
> in Phase 5 by the Validrow design and deleted. The Streamlit-specific findings
> below (`df_height`, `.stat .v`, the sidebar toggle, Streamlit's mobile
> breakpoint) no longer apply to any running code. The *rules* they established
> do — see Phase 5 for the two that were carried across.

Driven by ten reference screens (Sellfinity, sparkpixel, Untitled UI ×2, NexusAI,
Vocalyn, Jookë, Shakuro, Iron). They disagree on accent — orange, purple, blue,
black — so **accent was not taken from them**: `#1560d0` stays, because it is
contrast-verified and swapping it is a one-token change if ever wanted. What all
ten *do* agree on is structure, and that is what was adopted.

### What the references share, and what we now have

| Reference pattern | Before | Now |
|---|---|---|
| Persistent **topbar** — breadcrumb, account, system state | none; pages began at the title | sticky `.topbar`: breadcrumb, engine chip, help, workspace chip |
| **Grouped sidebar**, neutral raised active chip, real search, footer block | accent-filled active pill + rail; no search; status line only | white active chip on a tinted sidebar; working search; workspace summary card |
| **Page toolbar** of filter controls | filters buried inside the results card | page-level toolbar on Addresses; panel-scoped controls elsewhere |
| KPI: **icon tile → label → numeral + delta → caption → sparkline** | label → numeral → caption | full composition; `verdict_cards` now *is* `stat_card` |
| Panel headers with a **leading icon** | text only | every panel identified by icon |
| **Soft status pills** | dot + label, unfilled | dot + label in a soft-tinted pill |
| **Pagination** — "Showing 1–8 of 8" | virtualised scroll only | `page_slice` / `render_pager` on Addresses and job results |
| **Row selection → floating dark action bar** | none | multi-row select, sticky bar, export-selected |
| **Inline record detail** | none | one row selected opens `.detail` under the table |

The pill deserves a note, because it looks like a reversal of Phase 1's "never a
saturated pill". It isn't: the pill is a ~5%-alpha wash of the status's own
colour, the contents are still dot + text label, and the **label still carries
the meaning**. Every pair was re-measured on its own tint (below).

### Bugs this pass found in the running app

1. **`df_height()` stopped being exact under selection.** Enabling
   `on_select` adds a checkbox column, which pushed the grid past its container
   width; the resulting horizontal scrollbar ate 10px and clipped the last row
   to a sliver — the exact "blank strip under the table" Phase 3 removed.
   `df_height(selectable=True)` now budgets the scrollbar gutter.
2. **`.stat .v` was a descendant selector.** It reached into whatever a card's
   `extra` slot held, so the Exports legend inherited the 30px display numeral
   and rendered its counts as headlines. All stat internals are now `>` children:
   a card's anatomy must not style content placed inside it.
3. **The sidebar toggle was hidden.** `header[data-testid="stHeader"]` was hidden
   wholesale, which also hid `stExpandSidebarButton`. Invisible while the sidebar
   was pinned open — but it collapses to a drawer below Streamlit's mobile
   breakpoint, so **navigation was unreachable on a phone**. The button is now
   exempted and pinned top-left under 768px.
4. **`initial_sidebar_state="expanded"`** pinned that drawer *open* on mobile,
   covering the content. Now `"auto"`.
5. **A duplicate `style` attribute** on the Settings engine-status rows meant the
   colour never applied — the browser keeps the first and drops the second.
6. **`.stag` was used for a percentage** in the History and Exports lists. A
   percentage is a number, not a verdict; it now renders as a right-aligned
   numeric cell, and History gained a real **State** column (`job_state_tag`).
7. **A one-point chart drew a lone dot** in an empty grid, which reads as broken
   rather than as "not enough history". `volume_area` returns `None` below two
   points and the caller shows the empty state.
8. **`.rstrip("ly")`** on "Daily" — `rstrip` strips *characters*, not a suffix —
   produced "more than one dai".

### Contrast, re-measured on the new surfaces

Status labels now sit on their own soft tint, which was not in Phase 1's
measured set. Worst case across own-pill / white / canvas / inset:

| Verdict | Colour | On own pill | Worst of four |
|---|---|---|---|
| Deliverable | `#16744a` | 5.29 | 5.17 |
| Risky | `#9a6410` | 4.53 | 4.46 |
| Undeliverable | `#c43c2f` | 4.60 | 4.60 |
| Unknown | `#5b5f68` | **5.72** | **5.72** |

**Unknown moved `--n600` → `--n700`.** On its own `#f1f2f5` tint the old
`#6c707a` measured **4.43:1** — under AA, a regression the pill introduced. The
darker step reaches 5.72:1, stays a neutral ramp value (absence, not a fifth
category), and lifts the app's worst-case status contrast with it. Mirrored into
`--st-unk`, `grayColor`/`grayTextColor` and `chartCategoricalColors`.

Risky's 4.46 is the pre-existing bare-on-inset figure and is unchanged; in
practice the pill's own tint (4.53) is the surface it renders on.

Three micro-labels were introduced at **10px**, off the 8-style scale. Corrected
to `--fs-caption` (11px).

### Verification

- **Static audit** — 0 gradients, 0 hashed class selectors, 0 `blur()`, 0 raw hex
  outside `:root`. 83 tokens declared, 81 referenced (the two are `--st-unk-soft`
  and `--st-unk`, kept deliberately — see *Decisions*). Centring still confined
  to `.empty` and to marks.
- **`.st-key-*` selectors** are author-supplied container keys
  (`st.container(key=…)`), not generated class names — they do not violate the
  rule against hashed selectors.
- **Responsive genuinely confirmed this time.** Phase 3 could not resize the
  viewport (`innerWidth` stayed 1920). It now moves: verified at **1728**, **606**
  and **545** px — no horizontal overflow at any width (`scrollWidth ==
  clientWidth`), the mobile drawer opens and closes, and the topbar sheds the
  workspace subtitle, engine chip and crumb trail in that order.
- **All nine routes** render with no exception; single check, row selection,
  detail panel, pagination and the selection bar exercised in the browser.
- `pytest` — 58 passed. `ruff` — clean.

### Still not fixed

Items 1–6 of *Found but NOT fixed* below stand, with two updates:

- **Contacts pagination (item 4) is now client-side**, which is the display-layer
  half. Server-side paging still needs an API change.
- **Credits (item 1)** were deliberately *not* faked. The reference's sidebar
  footer is a usage meter with a quota bar; ours reports addresses and jobs
  instead, because there is no balance in the product and a progress bar against
  an invented denominator is the one thing this app cannot afford to ship.

The workspace chip says "Local workspace" and the engine host for the same
reason: there is no auth, so it names what is actually true rather than inventing
a signed-in person.

---

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

---

## Phase 5 — the Validrow design, server-rendered

The Streamlit front-end was replaced by the `Validrow.dc.html` design, rendered
from Python and served by the engine itself (`src/eve/web/`). Streamlit could not
express this design: it wraps everything in its own DOM, forces a top-down block
layout, and reruns the script per interaction, so the chart hover, expandable
rows, instant selection and sticky action bar would all have become page reloads.

The markup and inline styles are carried over from the design file verbatim. The
only additions are six CSS rules standing in for its `style-hover` attribute,
which has no CSS equivalent, and a self-hosted Inter face so the app renders with
no network access.

### What the design assumed, and what was built to make it true

The prototype's data was invented and always present. Four things had to become
real before the screens could report anything:

| Screen need | Built |
|---|---|
| "every address across all jobs, de-duplicated" | `eve/addresses.py` — a workspace read-model keyed by normalized address, so re-running a list refreshes rows instead of double-counting |
| live progress, elapsed, run duration | `phase`/`processed`/`total`/`started_at`/`finished_at` on `Job`, reported as the pipeline streams |
| the seven-layer trace | `eve/trace.py`, built only from `verdict.checks` |
| the dashboard's segment tabs | a **list type** chosen at upload, stored per address — the prototype assigned these by hashing a row id |

### Two rules carried across from Phase 4

Both were found by the Streamlit pass and both were violated by the first cut of
this one:

1. **A percentage may not claim an absolute it has not reached.** `share()`
   renders `<1%` for a non-zero count that rounds to zero and `>99%` for a
   partial that rounds to a hundred. A genuine zero or whole still reads plainly.
2. **A chart through one reading is a spike, not a trend.** Below two populated
   buckets the volume card states the limit and the sparklines are withheld,
   rather than drawing a shape that reads as broken.

### Where the trace refuses to guess

The engine runs classification *before* DNS, because a disposable domain settles
an address without a lookup. The product numbers DNS as layer 4 and
classification as 5. Rather than reorder the engine or imply a lookup that never
happened, the DNS row reads *"Not run — settled at layer 5"*. Likewise, with the
SMTP probe disabled layer 6 says so; it never fabricates a 250. Verified against
the mock MX: a confirmed mailbox reads `250 accepted — RCPT confirmed`, and a
catch-all's *identical* 250 is marked **Soft** with "acceptance proves nothing".

One bug this found: a DNS timeout was credited to layer 6. `TIMEOUT` is reported
by both the resolver and the probe, so the settling layer now depends on whether
the probe ran at all.

### Facts, not derivations

`settled_at` was stored at write time, so the DNS-timeout fix left every row
written before it claiming the wrong layer. The column stays for query
convenience, but the **fact** the rule needs — did the mailbox probe actually
run — is now stored alongside it as `smtp_ran`, and the settled-by-layer rollup
recomputes from that on read. A future change to the rule takes effect
immediately instead of waiting for every address to be re-validated.

`AddressStore.init()` carries the one migration this needed: `create_all` only
creates missing tables, so an existing workspace keeps its old columns. The
migration adds `smtp_ran`, backfills it from `checks`, and recomputes
`settled_at`. It is idempotent and covered by `tests/test_addresses_migration.py`,
which builds a database in the old shape and asserts the timeout row moves from
layer 6 to layer 4 while a genuine layer-6 result is left alone.

### Degraded is a real state now

The design has three engine states and the architecture made two of them
unreachable — the UI is served by the engine, so it cannot be offline. **DNS
off** is now reported as *degraded* (amber), because without an MX lookup
nothing about a domain can be proven and layers 4–7 never run. The SMTP probe
being off stays *online* with a banner: it is the documented default and it
produces honest Unknowns rather than wrong answers. The banner reports the most
serious condition rather than stacking one per flag.

### The identity block, resolved

The design puts a name and email address in the sidebar footer; there is still no
auth, so rendering them would be a claim the app cannot back up — exactly what
Phase 4 refused to do with credits. Neither dropping the block (losing the design)
nor hard-coding a person (inventing a fact) was acceptable.

**The component is unchanged and the content is declared, not fabricated.**
`EVE_WORKSPACE_NAME` / `EVE_WORKSPACE_EMAIL` are shown verbatim when set. Unset —
the default — the block reads **Local workspace** over the engine's own host, with
initials derived from the name rather than invented. Both lines are then true.

The chevron beside it implied a menu that did not exist. It now opens one, with
three destinations that do: Engine settings, API docs, and Copy engine URL. A
control that suggests an affordance has to honour it.

### The offline screen, removed

`offline.html` was deleted rather than kept as unreachable markup. Its copy rests
on a premise this architecture does not have:

> Validation needs a live engine. … **History and Addresses stay browsable.**

That sentence describes a client holding cached data *separately from* the engine.
Here the UI is served by the engine, so an engine that is not responding cannot
render the screen that says so — and History and Addresses, which read the same
process, would not stay browsable either. The screen cannot be made accurate
without rewriting its copy, at which point it is a different screen.

The two states that *can* occur are now both real and both reported (online,
degraded). The markup remains in `Validrow.dc.html` and in git history for the day
the web app becomes a true remote client, which is the only condition that makes
its copy true.

### Still open

- **Server-side paging** is now real on Contacts (Phase 4 left it client-side).
- **No auth.** Everything above is a workaround for its absence, not a substitute.
  When sessions land, `workspace_identity()` is the single place that changes.

---

## Phase 6 — the landing page, rebuilt

The first pass at `/` was a correct page with a template's manners. Its content
was already honest (every figure derived, nothing typed twice) and that survives
unchanged; what was replaced is the way it presented itself.

### What was actually wrong

1. **Alternating bands.** Five `.band` sections in `--canvas` against four in
   white. Two background colours doing the work of a rule is the single fastest
   way to make a page read as a template, and it also breaks the illusion that
   the page is one document: the eye reads nine stacked slabs rather than one
   argument.
2. **Cards floating on a field.** Six benefit cards, four verdict cards and
   three output cards, each with its own border and its own radius, gave the
   page 13 competing rectangles and no grid.
3. **No spatial memory.** Nothing told the reader where they were. Section
   eyebrows existed but scrolled away with their heading.
4. **The hero sold the wrong artifact.** It showed a verdict *mix*, which is a
   summary. The promise being made is "your file back, every column where you
   left it" — so the hero now shows the file.

### The system now

**One paper colour, one frame.** A single `--paper` column, max 1240px, held by
a hairline on both sides against a warm `--ground` with a faint 88px grid that
is only ever visible in the margins beside the frame. Sections are separated by
rules. There is no second background colour anywhere except one panel, below.

**A sticky rail.** Every section carries its caption (`03 / THE TRACE`) in a
168px left rail that sticks as the section scrolls. It is the same string as the
nav anchor, so a long page always says where you are without a scrollspy.

**Panels, not cards.** Grids of three or four are now one bordered panel divided
by hairlines. Thirteen rectangles became four.

**One dark moment.** The API section is the page's only inverted surface. Spent
once, it reads as emphasis; spent twice it would read as a theme.

**Two weights, one typeface.** 400 for running text, 600 for headings and UI
labels. The technical register comes from tracking, case and tabular figures,
never from a second family. Every size is a named role in the scale
(`--t-micro` through `--t-display`); nothing reaches for an arbitrary value.

### Three pictures that carry information

- **The hero sheet.** The uploaded file with the run's three columns appended,
  and a drawn rule between the columns that arrived and the columns that were
  added. The claim is the diagram.
- **The pipeline spine.** A hairline runs through the layer ordinals from 1 to
  7. A list of layers should look like a pipeline. The spine stops at the last
  ordinal rather than running past it (`.layer:last-child::before` masks the
  tail, because a container rule cannot know where the final circle sits).
- **The trace rail.** Seven bars per address: the layers that ran, the layer
  that settled it in that verdict's colour, and the layers it never reached,
  with the layer numbers ticked underneath. The settling layer is marked with a
  dot as well as a hue, so the picture still reads without colour vision.

### Contrast, re-measured

`--muted-2` (`#A8A29E`, 2.4:1) is the ramp step Phase 1 reserved for decorative
marks and never for text. It had been used for section indices, the rail's layer
numbers and the footer caption, all of which are read. Those move to `--muted`
(4.9:1) and the caption label goes to `--ink-3` (7.5:1), which is also the
hierarchy the rail wanted. The sample call's prompt and flags went from `#6F6C68`
on the dark panel (3.3:1) to `#8A8680` (4.6:1).

**Left as-is, deliberately:** the primary CTA is white on `--blue` (2.5:1). Every
primary action in the app is white on `#35ABFF`; matching it is the point, and
fixing it here alone would make the pitch and the product disagree about what a
primary action looks like. Fixing it in both places is a product decision.

### Motion

One beat: the four returned rows arrive in sequence, 90ms apart. The header's
rule fades in on scroll via `animation-timeline: scroll(root)` behind a
`@supports` guard, so the page needs no script at all; without support the rule
is simply always drawn.

### Verification

Rendered in headless Chrome at 500, 820 and 1440px and read section by section.
The 500px floor is Chrome's own clamp on a headless window, so a narrower real
phone viewport still wants a look. All 288 tests pass, including the invariants
that guard this page: the palette is the product's, the percentages sum to 100,
the layer count is spelled from the list, and the copy carries no em dashes.

---

## Phase 7 — the brand, and the API reference

### The brand set

Three colours changed and only three: the primary from `#35ABFF` to `#0000FF`,
the ink from `#101014` to `#0A0A0A`, and white from `#FFFFFF` to `#FCFCFC`. The
verdict hues and the neutral ramp are untouched, so nothing a colour *means* on
this product has moved.

The primary also settles the exception Phase 6 had to document. White on
`#35ABFF` measured **2.5:1**; white on `#0000FF` measures **8.6:1**. The primary
action now passes AA at any size, in the app and on the pitch, and the "matching
the product means keeping a failure" trade is gone rather than deferred.

**One consequence, taken deliberately.** The landing page's ground moved from
`#FAF9F7` to `#F2F2F3`. Against a `#FCFCFC` sheet the old warm ground was about
one percent away and the framed-sheet device stopped reading at all, and a warm
ground under a `#0000FF` brand reads as two systems in one page.

The check-in-a-rounded-square is gone. The wordmark is inlined once in
`_wordmark.html` and included by the landing page, the app shell, the auth
screens and the reference. Its letterforms are `currentColor`, so one file
serves a light page and a dark panel without a second asset; the two rules above
the *v* keep the brand blue behind `.wm-rule`, which is the hook a dark surface
overrides. The favicon is those same two rules.

**Not fixed:** the brand colours still live as literals in the app's screen
templates rather than as tokens those templates read. This pass rewrote the
literals mechanically. Tokenising the app shell is the right change and is a
larger one than a hue swap should carry.

### `/docs`, rebuilt

Swagger UI was retired (`docs_url=None`) and `/docs` is now rendered by
`eve.web.apidocs`. Two things were wrong with what it replaced. Swagger is
fetched from jsdelivr, so the one page documenting an engine that runs with no
network access was the one page that needed the network. And it looked like
Swagger, on a product whose two other public pages had just been rebuilt.

**It is generated, not written.** Every endpoint, parameter, field, type,
default and enum comes from `app.openapi()` at request time. A route added to a
router appears on the page; a field renamed in `eve.api.schemas` is renamed on
the page. The only strings typed in `apidocs.py` are the ones OpenAPI has
nowhere to put: what the base URL is, how a key is sent, and what the four
verdicts mean. The two numbers in "Getting started" are settings, so an engine
with a different upload cap documents its own.

That is also why `eve.api.schemas` grew a description on every public field. The
descriptions are not decoration for this page: they land in `/openapi.json`, so
a generated client and a reader get the same sentence.

**The samples are derived and had to be argued about.**

- A *request* sample carries the model's authored example if it has one, and
  otherwise the **required fields only**. An optional field filled with a type
  placeholder is not a neutral illustration, it is an instruction:
  `"check_dns": false` on the verify sample reads as "turn DNS off".
- A *response* sample is one authored example per model rather than one per
  field. Per-field examples cannot be coherent with each other: they would
  happily print `mx_found: true` beside a `no_mx` sub-status.
- Both are highlighted server-side. A page that documents an offline-capable
  engine should not fetch a highlighter to be readable.
- The curl block is assembled with its own markup rather than pattern-matched. A
  regex over a shell line cannot tell the port in a base URL from a number in a
  body, and it painted `127.0.0.1:8899` as three numeric literals.

**Layout.** The landing page's system exactly (framed column, hairlines, panels,
one dark surface), with a reference's shape rather than an argument's: a sticky
contents rail, and every endpoint a two-column block with what it takes on the
left and the call and the answer on the right. Below 1180px the two columns
stack rather than shrink; below 1080px the rail becomes a disclosure.

**Two things it does and one it does not.** A field whose type is another model
is followed one level and comes back as dotted rows (`mapping.email`), because
printing `ColumnMappingIn` and stopping names a type the page never defines. An
array response is unwrapped to its item and labelled "a list of these", because
`GET /v1/jobs` otherwise had a response section with no fields in it.

**Known and not fixed:** Pydantic sorts the keys of `json_schema_extra`
recursively on their way into the schema. Top-level keys are put back into
declaration order from the schema's own `properties`; a free-form nested object
has no property order to restore it from, so `checks` in the verify example
prints alphabetically rather than in pipeline order. The API returns it in
order; only the illustration is sorted. Restoring it would mean typing the layer
order into the docs module, which is the second copy this page exists to avoid.

### Verification

Rendered in headless Chrome at 500, 1000 and 1500px and read block by block. Six
new tests cover the page, and they assert against the OpenAPI document rather
than a list typed in the test: every route in the spec appears, every public
field carries a description, no type placeholder leaks into a sample, the upload
cap is the configured one, and the page loads nothing over the network.
