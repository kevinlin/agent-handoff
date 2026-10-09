---
name: Agent Handoff
description: Claude Code decides, a worker CLI executes — every handoff leaves a receipt.
colors:
  paper: "light-dark(#f1f3f6, #141519)"
  surface: "light-dark(#fbfcfd, #1c1e24)"
  ink: "light-dark(#171a20, #e8e9ec)"
  muted: "light-dark(#59616d, #99a1ae)"
  line: "light-dark(#cfd5de, #2d3038)"
  rule: "light-dark(#171a20, #6c7382)"
  hover: "light-dark(#eef1f5, #23262e)"
  cobalt: "light-dark(#1748d2, #93a9ff)"
  cobalt-soft: "light-dark(#e9efff, #1e2540)"
  cobalt-line: "light-dark(#c6d3fb, #33406c)"
  coral: "light-dark(#b53d34, #e2705a)"
  coral-ink: "light-dark(#713029, #f0a496)"
  ok-soft: "light-dark(#e4f1e8, #1b2d22)"
  ok-ink: "light-dark(#256240, #5fbe84)"
  ok-line: "light-dark(#bcdfc8, #2f5a3f)"
  bad-soft: "light-dark(#fff0ed, #32201c)"
  bad-ink: "light-dark(#8d3227, #e88b74)"
  bad-line: "light-dark(#f0c5b8, #5e3229)"
typography:
  display:
    fontFamily: "Georgia, \"Times New Roman\", serif"
    fontSize: "clamp(30px, 4vw, 50px)"
    fontWeight: 600
    lineHeight: 0.96
    letterSpacing: "-0.045em"
  figure:
    fontFamily: "Georgia, \"Times New Roman\", serif"
    fontSize: "clamp(20px, 2.1vw, 30px)"
    fontWeight: 600
    lineHeight: 1.05
    letterSpacing: "-0.035em"
    fontFeature: "\"tnum\""
  title:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "15px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "0.06em"
  body:
    fontFamily: "ui-sans-serif, -apple-system, \"Segoe UI\", sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "11px"
    fontWeight: 750
    lineHeight: 1.3
    letterSpacing: "0.09em"
  code:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.55
rounded:
  hairline: "4px"
  inner: "8px"
  card: "16px"
  pill: "999px"
spacing:
  xs: "8px"
  sm: "12px"
  md: "16px"
  gutter: "clamp(1rem, 3vw, 2.5rem)"
components:
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.card}"
    padding: "8px"
  tab:
    textColor: "{colors.muted}"
    typography: "{typography.label}"
    height: "44px"
    padding: "0 0.9rem"
  tab-selected:
    textColor: "{colors.ink}"
  button-action:
    backgroundColor: "{colors.cobalt-soft}"
    textColor: "{colors.cobalt}"
    typography: "{typography.label}"
    rounded: "{rounded.inner}"
    height: "36px"
    padding: "0 0.9rem"
  button-link:
    textColor: "{colors.cobalt}"
  select:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.code}"
    rounded: "{rounded.inner}"
    height: "36px"
    padding: "0.4rem 0.5rem"
  pill-done:
    backgroundColor: "{colors.ok-soft}"
    textColor: "{colors.ok-ink}"
    rounded: "{rounded.pill}"
    padding: "0.1rem 0.5rem"
  pill-failed:
    backgroundColor: "{colors.bad-soft}"
    textColor: "{colors.bad-ink}"
    rounded: "{rounded.pill}"
    padding: "0.1rem 0.5rem"
  pill-running:
    backgroundColor: "{colors.cobalt-soft}"
    textColor: "{colors.cobalt}"
    rounded: "{rounded.pill}"
    padding: "0.1rem 0.5rem"
  banner:
    backgroundColor: "{colors.cobalt-soft}"
    typography: "{typography.code}"
    padding: "0.7rem 0.9rem"
  banner-warning:
    backgroundColor: "{colors.bad-soft}"
    textColor: "{colors.coral-ink}"
---

# Design System: Agent Handoff

## Overview

**Creative North Star: "The Flight Recorder"**

A Handoff page is opened after the run has finished. The reader is inspecting a black box: what ran on which backend, what failed, what it cost. The design behaves like a recorder's readout. Every lane, row and figure maps to a file on disk. Failures show in Signal Coral, and nothing is smoothed into a success story. The serif masthead names the run once. Everything below it is instrumentation set in monospace capitals and tabular figures.

The pages are dense but legible. Tables with nine to twelve columns are normal, so sticky headers and sticky first columns let a wide job row scroll inside its own container instead of pushing the page sideways. Tap targets stay at 44px even when the type is 11px. Colour always backs up a word and never replaces it. Light and dark are a single token set written with `light-dark()`.

Known drift: the setup wizard (`scripts/handoff-setup-ui.py`) runs a separate warm system with Avenir, an `#e75b38` accent, dark slab panels, brown-tinted shadows, a noise texture and staggered entrance motion. The transcript viewer is a near-variant with its own accent (`#4a5cd6`), and the user guide uses a slate palette. This document is the target those surfaces converge toward. It does not describe them.

**Key Characteristics:**
- One serif moment (the run's title), then mono capitals for every label, heading and tab.
- Flat surfaces on cool paper; depth comes from 1px lines and a 2px ink rule, not shadows.
- Two accents with fixed jobs: Ledger Cobalt navigates and marks live state, Signal Coral warns.
- State always appears as a tinted pill with the word inside it.
- Measured and unmeasured values are visibly different types.
- Motion is almost absent. The furniture reveals once on load, and views switch instantly.

## Colors

Cool, low-chroma paper and ink, with two saturated accents that each have one job.

### Primary
- **Ledger Cobalt** (`cobalt`): links, the selected-tab underline, focus rings, running-state pills, section labels (h3), the masthead subtitle, and serif figures in the cost receipt. Its soft and line tints (`cobalt-soft`, `cobalt-line`) back the info banner and the action button.

### Secondary
- **Signal Coral** (`coral`, `coral-ink`): the warning banner's left rule, the cost receipt's caveat rule, and the outline that "Show hotspots" puts on the overview diagram. Coral is reserved for "read this carefully" and never decorates.

### Neutral
- **Instrument Paper** (`paper`): page background.
- **Panel White** (`surface`): cards, tables, code blocks, iframes.
- **Recorder Ink** (`ink`): body text and selected tab labels.
- **Muted Slate** (`muted`): table headers, h2 and h4, unselected tabs, and unmeasured values.
- **Hairline** (`line`): every 1px border and row divider.
- **Ink Rule** (`rule`): the 2px masthead underline and the cost receipt's `thead` border.
- **Row Hover** (`hover`): table row hover, on fine pointers only.

### Status
- **Done** (`ok-soft` / `ok-ink` / `ok-line`): completed jobs, success status text.
- **Failed** (`bad-soft` / `bad-ink` / `bad-line`): failed and cancelled jobs, the warning banner background.

### Named Rules
**The Two Jobs Rule.** Cobalt means "go here" or "this is live". Coral means "careful". Neither appears in any other role.

**The Word Inside Rule.** State is never colour alone. A pill always contains the state word (`DONE`, `FAILED`, `RUNNING`).

## Typography

**Display Font:** Georgia (with "Times New Roman", serif)
**Body Font:** ui-sans-serif (with -apple-system, "Segoe UI", sans-serif)
**Label/Mono Font:** ui-monospace (with SFMono-Regular, Menlo, Consolas, monospace)

**Character:** The serif is the run's nameplate and the cost receipt's figure face, set tight. The monospace capitals are the recorder's printed channel labels. Neither is a web font. Pages load nothing from outside.

### Hierarchy
- **Display** (600, clamp(30px, 4vw, 50px), 0.96): the masthead h1, once per page. Balanced wrap.
- **Figure** (600, clamp(20px, 2.1vw, 30px), 1.05, tabular): headline metrics in the cost receipt, in cobalt.
- **Title** (mono 600, 15px, 1.3, 0.06em, uppercase): h2 section headings, in muted.
- **Body** (400, 14px, 1.6): running prose and notes; cap notes at 68ch.
- **Label** (mono 750, 11px, 0.09em, uppercase): tabs, h3, table headers, form labels, the toggle, and summaries. Tabs use weight 600 with 0.08em tracking.
- **Code** (mono 400, 13px, 1.55): `pre`, banners, selects, meta values.

### Named Rules
**The One Serif Rule.** The serif appears in the masthead and in measured figures. It never appears in a label, a button or a table cell.

**The Unmeasured Rule.** An absent value is never set in the figure face. It renders as muted italic mono with a word ("unknown", "≥ 1,204 (3 of 5)"), so a reader cannot mistake a gap for a reading.

## Layout

A single column, `max-width: 90rem`, centred, with `clamp(1rem, 3vw, 2.5rem)` side gutters and a 16px gap between blocks. The grid column is always `minmax(0, 1fr)`, never `auto`, so a wide table cannot drag the page sideways. Wide content (the 1600-unit timeline diagram, the nine-column job table) stacks at full measure instead of sharing a row.

Tables scroll inside their own bordered container, with a sticky header row and a sticky first column (the job id). The cost receipt's metric grid uses `repeat(auto-fit, minmax(10rem, 1fr))` so its figures wrap rather than scroll. Key and value pairs use a two-column `auto / minmax(0, 1fr)` grid, which goes to four columns from 62rem. The minimum page width is 320px.

**The Own-Scroll Rule.** Anything wider than the viewport scrolls in its own container. The page itself never scrolls horizontally.

## Elevation & Depth

Flat. No component has a shadow. Surfaces separate from the paper by tone (`surface` on `paper`) and a 1px `line` border. Structure comes from rules: a 2px ink rule under the masthead and a 3px cobalt underline on the selected tab. The cost receipt's lead cards get a 7px cobalt top border, and banners get a 3px left rule. The only overlay is the diagram's hotspot layer, which is absolutely positioned and has no lift.

**The Ruled, Not Lifted Rule.** Hierarchy comes from lines and rules. A shadow on a Handoff surface is drift.

## Shapes

Concentric radii: the outer card is 16px, and inner surfaces (tables, code blocks, the diagram frame, iframes, buttons, selects) are 8px, which is 16 minus the card's 8px padding. Pills are fully round. Hotspots and focus rings use 4px. Banners round only their right side (`0 8px 8px 0`) so the left rule stays square. Tabs round only their top corners.

## Components

### Buttons
Small, uppercase and mono, sitting next to the data.
- **Shape:** gently rounded (8px).
- **Action:** cobalt text on `cobalt-soft` with a `cobalt-line` border, 36px tall, label type. Pressing scales it to 0.97, which is off under reduced motion.
- **Link button:** bare cobalt mono 12px with an underline offset 2px. Used for row actions such as opening a job's transcript.
- **Focus:** a 3px cobalt outline offset 3px, on every focusable element.

### Tabs
Instant, many-times-a-minute switches.
- 44px tall, label type, muted when unselected. Selected is ink with a 3px cobalt bottom border. The tablist sits on a 1px `line` rule.
- No transition, and the panel it shows does not animate in.

### Chips (status pills)
- **Style:** fully round, 1px border, mono 11px uppercase with 0.06em tracking.
- **State:** done (ok tints), failed and cancelled (bad tints), running (cobalt tints).

### Cards / Containers
- **Corner Style:** 16px.
- **Background:** `surface` on `paper`.
- **Shadow Strategy:** none (see Elevation & Depth).
- **Border:** 1px `line`. Cost receipt lead cards add a 7px cobalt top border.
- **Internal Padding:** 8px when the card frames a diagram or table; 19px 20px when it frames figures and prose.

### Inputs / Fields
- **Select:** code type, 36px tall, 8px radius, 1px `line` border on `surface`. Its label is set in label type next to it.
- **Focus:** the shared cobalt outline.

### Banners
- **Info:** a 3px cobalt left rule on `cobalt-soft`, in code type.
- **Warning:** a 3px coral left rule on `bad-soft`, with `coral-ink` text.

### Data Table (signature)
- Separate borders with 0 spacing, tabular numerals, `nowrap` cells, and a 1px `line` divider on every row except the last.
- Header row: sticky, `surface` background, mono 750 10px uppercase in muted.
- First column: sticky, and stays on top of the header where they cross.
- Row hover on fine pointers only.

### Timeline Diagram with Hotspots (signature)
- The diagram box equals the image box exactly (block image, 8px radius, a 1px inset outline at low alpha) so percentage hotspots map onto the viewBox.
- Hotspots are invisible until "Show hotspots" is on, which outlines each one in 2px coral with a coral wash. They have no hover or press transform, because any transform moves them off the bar they cover.

## Do's and Don'ts

### Do:
- **Do** pull every colour from the token set; light and dark come from the same `light-dark()` declaration.
- **Do** put a word inside every state indicator, and use the ok, bad or cobalt pill tints.
- **Do** keep tap targets at 44px (tabs, the toggle) or 36px (action buttons, selects) even when the type is 11px.
- **Do** give wide tables their own scroll container with a sticky header and a sticky first column.
- **Do** render missing measurements as muted italic mono words, never as a figure or a dash.
- **Do** keep the one-time reveal (460ms, `cubic-bezier(.2,.72,.2,1)`, 10px rise) to the masthead and the tablist, and collapse it under `prefers-reduced-motion`.
- **Do** limit the one authored moment to the verdict stamp: the pill that closes a run (session view) or an install (setup wizard) lands once from scale 1.18 and -3deg, transform only, 380ms. It is off under `prefers-reduced-motion`.

### Don't:
- **Don't** add shadows, gradients, glows or noise textures. Those belong to the setup wizard's drifted system.
- **Don't** use coral for anything that isn't a warning, a caveat or a hotspot outline.
- **Don't** animate tab switches or panel reveals.
- **Don't** put a hover lift or press transform on a hotspot.
- **Don't** load web fonts, CDN scripts or any third-party resource. Pages run on loopback and stay self-contained.
- **Don't** set labels, buttons or table cells in the serif.
