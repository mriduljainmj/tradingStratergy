# Axiom Terminal — UI/UX Architecture & Structural Specification

**Product:** Enterprise Stock Screener + Algo Strategy Builder
**Surface:** Desktop web, single-page application, ≥1440px optimized (min 1280px)
**Theme:** Near-black OLED dark mode, violet accent, institutional data-density
**Stack assumed by blueprints:** React + Tailwind CSS (CSS-variable token bridge)
**Status:** Implemented reference build = `Axiom Terminal.html`

---

## 0. Design Principles

| # | Principle | Rule |
|---|-----------|------|
| 1 | Density first | 30px row height, 11–13px type, hairline `rgba(255,255,255,.055)` dividers. No decorative whitespace. |
| 2 | Mono for data | Every number, price, code, timestamp uses `JetBrains Mono` + `font-variant-numeric: tabular-nums`. UI chrome uses `Inter`. |
| 3 | Color = meaning | Hue is reserved. Violet = interactive/active. Green/red = bull/bear only. Amber = caution/friction. Neutral grays carry everything else. |
| 4 | Layered surfaces | 7-step neutral ramp (`void → base → panel → elevated → input → hover → active`) conveys depth without shadows. Shadows only on floating nodes/tooltips. |
| 5 | Zero fatigue | Max chroma on backgrounds ≈ 0. Bright fills appear only on <2% of pixels (active states, live ticks). |
| 6 | Non-destructive sync | Multi-chart sync and filter logic are additive, reversible, and visibly state-flagged. |

---

## 1. Information Architecture (IA) Map

```
AXIOM TERMINAL
│
├── GLOBAL CHROME (persistent, all workspaces)
│   ├── Icon Rail ............ workspace switch · logo · settings
│   ├── Top Bar .............. breadcrumb · ⌘K command · market clocks · alerts · account
│   ├── Ticker Tape .......... live consolidated quotes (hover = pause)
│   └── Status Bar ........... feed health · latency · seq · resource meters · build
│
├── 1 · SCREENER  (default workspace)
│   ├── Filter Builder (left, 320px)
│   │   ├── Saved Screen selector
│   │   ├── Logic Tree → Group(AND/OR) → {Rule | nested Group}
│   │   │      Rule = field · operator · value(inline-edit) · remove
│   │   ├── Quick-Add field palette
│   │   └── Match counter (live)
│   └── Result Matrix (right, fluid)
│       ├── Active-filter tag bar (editable/removable)
│       ├── Toolbar → sort stack · columns · export · save
│       └── Data table → sticky header · multi-sort · sparkline · vol bars · RSI gauge
│           └── Row select → (future) symbol detail drawer
│
├── 2 · CHARTS  (Multi-Chart Workspace)
│   ├── Workspace Bar → layout picker(1×1·2×2·1+3·3×1) · Sync Hub · group legend · save
│   └── Chart Grid → ChartCell[]
│       └── ChartCell → header(symbol·px·Δ·interval·group·tools) + canvas
│              canvas = candles · volume · SMA · trade markers · crosshair · price axis
│
├── 3 · STRATEGY  (No-Code Builder)
│   ├── Block Palette (left, 210px) → Data · Indicators · Logic · Risk · Execution
│   ├── Node Canvas (center) → draggable nodes + bezier edges
│   └── Inspector (right, 420px)  [split-screen]
│       ├── Risk Controls (capital · size · stops · margin · exposure bar)
│       └── Generated Python (Backtrader, live-syntax-highlighted)
│
└── 4 · BACKTEST  (Insights Dashboard)
    ├── Run Header → strategy · status · period · params · export · re-run
    ├── KPI Matrix (8 cells)
    ├── Equity Curve + Underwater/Drawdown
    ├── Monthly Returns Heatmap
    ├── Return Distribution histogram
    ├── Friction Analyzer
    └── Trade Log (searchable, chart-linked)
```

**Primary navigation flow:** `Screener → (select symbols) → Charts → (define logic) → Strategy → Run → Backtest → iterate`. The Icon Rail makes all four workspaces O(1) reachable; no nested routing.

---

## 2. Design Tokens

### 2.1 Surfaces (neutral depth ramp)

| Token | Hex | Tailwind key | Usage |
|-------|-----|--------------|-------|
| `--bg-void` | `#060709` | `void` | App backdrop, ticker/status bars, code pane |
| `--bg-base` | `#0a0c10` | `base` | Workspace background, table body |
| `--bg-panel` | `#0e1117` | `panel` | Panel/card surface |
| `--bg-elevated` | `#141821` | `elevated` | Panel & table headers, raised chrome |
| `--bg-input` | `#181d27` | `input` | Inputs, chips, segmented controls |
| `--bg-hover` | `#1b2230` | `hover` | Row / control hover |
| `--bg-active` | `#20283a` | `active` | Pressed / selected |

### 2.2 Borders

| Token | Value | Use |
|-------|-------|-----|
| `--line` | `rgba(255,255,255,.055)` | Default hairline (dividers, rows) |
| `--line-2` | `rgba(255,255,255,.09)` | Control borders |
| `--line-strong` | `rgba(255,255,255,.14)` | Hover/emphasis borders |

### 2.3 Text

| Token | Hex | Role |
|-------|-----|------|
| `--tx-hi` | `#e9ecf2` | Primary (symbols, values, headings) |
| `--tx` | `#aeb6c4` | Body |
| `--tx-lo` | `#6c7686` | Secondary, labels |
| `--tx-dim` | `#4a5260` | Muted, axis ticks, disabled |

### 2.4 Accent — Violet (interactive)

`--ac #a78bfa` · `--ac-hi #c4b1ff` · `--ac-dim #7c6cf0`
Tints: `--ac-bg rgba(167,139,250,.13)` · `--ac-bg-2 …07` · `--ac-line …40` · `--ac-glow …55`

### 2.5 Semantic market colors

| Token | Hex | Meaning |
|-------|-----|---------|
| `--bull` / `--bull-hi` | `#18c98d` / `#34e6a8` | Up / gain / long / oversold-buy |
| `--bear` / `--bear-hi` | `#ff4d6a` / `#ff7088` | Down / loss / short / overbought-sell |
| `--warn` | `#f5a524` | Caution, slippage, exposure ceiling |
| `--info` | `#38bdf8` | Neutral data / fundamentals |
| Sync groups | `#a78bfa · #38bdf8 · #f5a524 · #fb7185` | grp-1…4 |

> **Convention:** Western — green up, red down. Each semantic color ships a `-bg` tint at 12% for fills/badges so saturated hues never touch large areas.

### 2.6 Typography

```
--ui:   "Inter", -apple-system, system-ui, sans-serif      /* chrome */
--mono: "JetBrains Mono", ui-monospace, "SF Mono", monospace /* all data + code */
```

| Role | Size / Weight | Notes |
|------|---------------|-------|
| Base body | 13px / 400 | line-height 1.4 |
| Table cell | 12.5px / 400–600 | `tabular-nums`, `tnum`, `zero` features on |
| Micro-label (`.eyebrow`) | 9.5px / 600 | uppercase, `letter-spacing .13em` |
| KPI value | 23px / 600 | mono, `letter-spacing -.01em` |
| Panel title | 11.5px / 600 | |
| Code | 11.5px / 400 | line-height 1.65 |

### 2.7 Radii & Density

```
--r-xs 3px · --r-sm 5px · --r-md 7px · --r-lg 10px
--row-h 30 · --bar-h 46 · --rail-w 56 · --tape-h 30 · --status-h 26   (px)
```

### 2.8 Tailwind config bridge

```js
// tailwind.config.js — mirrors the CSS-variable source of truth
export default {
  theme: {
    extend: {
      colors: {
        void:'#060709', base:'#0a0c10', panel:'#0e1117', elevated:'#141821',
        input:'#181d27', hover:'#1b2230', active:'#20283a',
        txhi:'#e9ecf2', tx:'#aeb6c4', txlo:'#6c7686', txdim:'#4a5260',
        ac:{DEFAULT:'#a78bfa', hi:'#c4b1ff', dim:'#7c6cf0'},
        bull:{DEFAULT:'#18c98d', hi:'#34e6a8'},
        bear:{DEFAULT:'#ff4d6a', hi:'#ff7088'},
        warn:'#f5a524', info:'#38bdf8',
      },
      borderColor: { line:'rgba(255,255,255,.055)', line2:'rgba(255,255,255,.09)', lineStrong:'rgba(255,255,255,.14)' },
      fontFamily: { ui:['Inter','system-ui','sans-serif'], mono:['"JetBrains Mono"','ui-monospace','monospace'] },
      borderRadius: { xs:'3px', sm:'5px', md:'7px', lg:'10px' },
      spacing: { rail:'56px', bar:'46px', tape:'30px', status:'26px', row:'30px' },
      fontSize: { eyebrow:['9.5px',{letterSpacing:'.13em',fontWeight:'600'}] },
    },
  },
}
```

---

## 3. Layout Architecture

### 3.1 Application shell — CSS grid blueprint

The shell is a single 2-col × 4-row grid. The rail spans all rows; the right column stacks top bar → tape → main → status. `main` is the only scroll/overflow owner per workspace.

```css
.app {
  display: grid;
  grid-template-columns: var(--rail-w) 1fr;          /* 56px | fluid */
  grid-template-rows: var(--bar-h) var(--tape-h) 1fr var(--status-h);
  grid-template-areas:
    "rail topbar"
    "rail tape"
    "rail main"
    "rail status";
  height: 100vh;
}
.main { grid-area: main; overflow: hidden; position: relative; }
```

```jsx
// Tailwind equivalent
<div className="grid grid-cols-[56px_1fr] grid-rows-[46px_30px_1fr_26px] h-screen bg-base">
  <Rail   className="row-span-4" />
  <TopBar    className="row-start-1 col-start-2" />
  <Tape      className="row-start-2 col-start-2" />
  <main      className="row-start-3 col-start-2 overflow-hidden relative" />
  <StatusBar className="row-start-4 col-start-2" />
</div>
```

### 3.2 Window-state management rule

> Every workspace mounts inside `main` and owns its own internal grid. **`main` clips (`overflow:hidden`); inner panels scroll.** This keeps the shell chrome fixed while panels resize/dock/fullscreen, and prevents nested scroll-chaining. Fixed-px tracks (rail 56, sidebars 320/210/420) + one `1fr` track per workspace guarantees the data region absorbs all resize slack.

---

## 4. Component-by-Component UX Specification

> Format per component: **Structure** · **Interactions / States** · **Keyboard** · **Responsive**.

### 4.1 Icon Rail & Workspace Switcher

- **Structure:** 56px fixed column on `--bg-void`. Logo glyph (gradient violet, glow) → 4 workspace buttons → spacer → settings. Each button 40×40, 16px icon.
- **States:**
  - default `--tx-lo`; hover `--tx-hi` on `--bg-hover`;
  - active = `--ac-hi` text on `--ac-bg` + 3px violet left indicator bar with glow (`::before`).
  - Tooltip slides in from `left:48px` on hover (120ms opacity).
- **Keyboard:** `1‒4` jump to workspace; `,` opens settings; tooltips mirror to `aria-label`.
- **Responsive:** Width constant; below 1280px the labels stay tooltip-only (already icon-first).

```jsx
<button className="relative grid place-items-center w-10 h-10 rounded-md text-txlo
  hover:text-txhi hover:bg-hover data-[on=true]:text-ac-hi data-[on=true]:bg-ac/[.13]
  before:content-[''] before:absolute before:-left-2.5 before:inset-y-2 before:w-[3px]
  before:rounded-r before:bg-ac before:opacity-0 data-[on=true]:before:opacity-100" />
```

### 4.2 Top Bar / Command

- **Structure:** breadcrumb (`H1 14px` + sub 11px) · centered search (max 460px, `⌘K` kbd hint) · right cluster: MARKET OPEN badge, NYSE/LSE/TSE clocks (live, 1s tick, open-market in green), bell w/ alert dot, avatar.
- **States:** search border `--line → --line-2` on hover; clocks colorize `t.open` green.
- **Keyboard:** `⌘K`/`Ctrl K` focuses search → palette (symbols, screens, strategies); `Esc` clears.
- **Responsive:** <1366px the LSE/TSE clocks drop first; search shrinks to `flex-1`.

### 4.3 Ticker Tape

- **Structure:** 30px void strip; duplicated symbol set (`[...U,...U]`) translated `-50%` over 80s linear loop. Each item: `sym · px(mono) · ▲/▼ Δ%`.
- **Interaction:** `mouseenter` sets `app[data-paused="1"]` → `animation-play-state:paused` (read without chase). Live values re-render via `LiveBus` subscription.
- **Responsive:** Purely decorative-informational; hidden `<1200px` via `tape:hidden`.

### 4.4 Status Bar

- Segments: `● LIVE · CONSOLIDATED FEED` (pulsing dot), `WS CONNECTED`, `LATENCY {n}ms` (amber >16), `SEQ`, right cluster `CPU · MEM · build`. All mono 11px. Read-only telemetry.

### 4.5 Screener — Filter Builder (sidebar)

- **Structure (320px panel):** header → saved-screen selector → **recursive Logic Tree** → Quick-Add palette → live match footer (big violet count).
- **Logic Tree model:**
  ```ts
  type Node = Group | Rule
  Group = { id, op:'AND'|'OR', children: Node[] }
  Rule  = { id, field, cmp:'gt'|'lt'|'gte'|'lte', value }
  evaluate(Group,row) = op==='AND' ? children.every : children.some
  ```
  Group chrome: 2px left border (violet=AND, amber=OR), `ALL OF / ANY OF` toggle badge, rule count, nest depth tints background `panel → void`.
- **Editable removable tags (hero interaction):** each Rule renders as a `.chip` —
  - category dot (Fundamental=info / Technical=violet / Alt=amber),
  - field label, **operator toggle** (click cycles `> ≥ < ≤`),
  - **value:** click → inline `<input>` (44px, violet ring) → `Enter`/blur commits `parseFloat`,
  - `✕` remove (red hover).
- **States:** chip hover lifts value bg; group add buttons ghost; counts + table recompute synchronously on every edit (`useMemo([tree,sorts])`).
- **Keyboard:** `Tab` across chips; `Enter` commit; `Esc` cancel edit; `⌫` on focused chip removes.
- **Responsive:** Sidebar fixed 320; below 1366 collapsible to a 0px drawer toggled from toolbar.

### 4.6 Screener — Result Matrix

- **Structure:** active-tag bar → toolbar (result count · sort stack · Columns · Export · Save Screen) → `<table.matrix>`.
- **Sticky header:** `position:sticky; top:0; z-5` on `--bg-elevated`; uppercase 10.5px labels.
- **Multi-column sort:** click cycles `desc → asc → off`; **Shift-click appends** to the sort stack; header shows arrow + ordinal badge; active sorts echoed as violet chips in toolbar.
- **Cells:** star toggle · symbol (mono avatar + name) · **live Last** (`LiveNum` flashes green/red 560ms) · Δ% colored · **price sparkline** (gradient area) · Vol · **20d volume micro-bars** · Mkt Cap · P/E · **RSI gauge** (zone-colored bar: ≥70 red, ≤30 green) · Beta · sector badge.
- **States:** row hover `--bg-hover`; selected row `--ac-bg-2` + inset 2px violet rail.
- **Keyboard:** `↑/↓` move selection, `Space` star, `Enter` open detail, `⌘C` copy row.
- **Responsive:** `overflow-x:auto`; star/Beta/Sector are first to hide under 1280px via column manager.

### 4.7 Multi-Chart Workspace + Sync Hub

- **Workspace Bar:** Layout picker (1×1 · 2×2 · 1+3 · 3×1 as 14px glyph buttons) · **Sync Hub** (Symbol / Interval / Crosshair / Indicators toggle pills) · group legend · Link All · Save Workspace.
- **Grid:** `display:grid; gap:1px; background:var(--line)` → 1px hairline seams between cells. Layout objects set `grid-template-columns/rows`; the 1+3 asymmetric promotes cell 0 to `grid-row:1/4`.
- **ChartCell:** header (group dot · symbol · live px · Δ% · interval segmented · gear · expand) + canvas. Focused cell shows inset violet ring.
- **Canvas (SVG, `preserveAspectRatio="none"`, `vector-effect:non-scaling-stroke`):** candles (body rect + wick), volume sub-pane (lower 24%), SMA(9) overlay, dashed entry(green)/exit(red) verticals, last-price dashed line + axis tag, HTML-overlay price ladder, BUY/SELL execution badges.
- **Sync semantics:**
  - *Interval/Symbol:* editing a cell propagates to all cells sharing its **sync group** color.
  - *Crosshair:* `onMouseMove` publishes `{x,y,group}` as fractions; subscribers in-group render shared violet crosshair. Off → crosshair stays local to focused cell.
  - Group dot click cycles a cell between groups (1↔2).
- **Keyboard:** `1·2·3·4` layout presets; `S` toggle symbol-sync; `X` crosshair-sync; `F` fullscreen focused cell; `[` `]` cycle interval.
- **Responsive:** below 1280, 2×2 reflows to 1×N stack; sync logic unaffected.

### 4.8 Strategy Canvas

- **Palette (210px):** grouped draggable blocks (Data/Indicators/Logic/Risk/Execution), each with type-colored icon chip; hover nudges `translateX(2px)`.
- **Canvas:** dotted-grid background (`radial-gradient 22px`). Nodes absolutely positioned, 184px, type-tinted header gradient, 2 ports (in left / out right). **Drag:** `mousedown` captures offset → `window mousemove` updates `{x,y}` → edges (cubic bezier, horizontal control offset) recompute live. Active node gets violet ring + shadow.
- **Edge color** inherits the source node's type color at 55% opacity, drawn in a single SVG layer beneath nodes (`pointer-events:none`).
- **Inspector (420px, split-screen):**
  - *Risk Controls:* labeled rows with mono value inputs (capital, max size, trailing stop, daily loss, margin, max positions) + **portfolio exposure bar** (green→amber gradient, % / limit).
  - *Generated Python:* Backtrader class, token-highlighted (`kw/fn/str/num/com/cls` color classes), header flags `BACKTRADER` + live pulse. Values mirror node/risk params.
- **Keyboard:** `Del` remove node, `⌘D` duplicate, `⌘↵` Run Backtest, drag from palette to add.
- **Responsive:** Inspector collapses to tabbed overlay <1440; canvas pans (future) when nodes exceed viewport.

### 4.9 Backtest Dashboard

- **Run Header (sticky):** strategy selector · COMPLETE badge · period/params · Export · Re-run.
- **KPI Matrix:** `grid-cols-4` hairline-separated cells; label(10px uppercase) + value(23px mono, semantic color) + meta. 8 metrics: Total Return, CAGR, Sharpe, Sortino, Max Drawdown, Win Rate, Profit Factor, Avg Slippage.
- **Equity Curve:** strategy area (violet gradient) + dashed benchmark line; underwater/drawdown strip below (red area). SVG `preserveAspectRatio="none"`, non-scaling strokes; HTML legend overlay.
- **Monthly Returns Heatmap:** `grid-cols-[26px_repeat(12,1fr)]`; cell intensity = `|return|/8` opacity over green/red; title-tooltip per cell.
- **Return Distribution:** flex histogram, green/red buckets, μ/σ footer.
- **Friction Analyzer:** Commission / Slippage / Spread / Net rows (mono, `nowrap`).
- **Trade Log:** sticky-header table, symbol filter input, side badges (LONG bull / SHORT bear), colored P&L + return, duration, chart-link affordance.
- **Keyboard:** `/` focus trade filter; row `Enter` deep-links to Charts at trade timestamp.
- **Responsive:** body `grid-cols-[1fr_340px]` → single column <1366; KPI matrix `4→2` cols.

---

## 5. Tailwind Structural Blueprints (safe window states)

### 5.1 Screener split

```jsx
<div className="grid grid-cols-[320px_1fr] h-full">
  <aside className="flex flex-col bg-panel border-r border-line min-h-0">
    <header className="h-[34px] flex items-center gap-2 px-3 bg-elevated border-b border-line shrink-0" />
    <div className="flex-1 overflow-y-auto p-3" /> {/* logic tree */}
    <footer className="border-t border-line px-3.5 py-2.5 bg-elevated flex items-center justify-between" />
  </aside>
  <section className="flex flex-col min-w-0">
    <div className="flex flex-wrap items-center gap-2 px-3.5 py-2 border-b border-line bg-base" /> {/* tags */}
    <div className="flex items-center justify-between px-3.5 py-2 border-b border-line bg-panel" /> {/* toolbar */}
    <div className="flex-1 overflow-y-auto bg-base">
      <table className="w-full border-separate border-spacing-0">
        <thead><tr>{/* th: sticky top-0 z-[5] bg-elevated */}</tr></thead>
        <tbody>{/* tr: h-row hover:bg-hover [&.sel]:shadow-[inset_2px_0_0_theme(colors.ac.DEFAULT)] */}</tbody>
      </table>
    </div>
  </section>
</div>
```

### 5.2 Multi-chart grid (hairline seams + asymmetric promotion)

```jsx
const LAYOUTS = {
  '1x1':'grid-cols-1 grid-rows-1',
  '2x2':'grid-cols-2 grid-rows-2',
  '1+3':'grid-cols-[2fr_1fr] grid-rows-3',  // cell0 → row-span-3
  '3x1':'grid-cols-3 grid-rows-1',
};
<div className={`flex-1 grid gap-px bg-line p-px min-h-0 ${LAYOUTS[layout]}`}>
  {cells.map((c,i)=>(
    <div key={i}
      className={`flex flex-col min-h-0 overflow-hidden bg-panel
        ${layout==='1+3'&&i===0 ? 'row-start-1 row-end-4 col-start-1' : ''}
        ${focus===i ? 'shadow-[inset_0_0_0_1.5px_theme(borderColor.line2)]' : ''}`}>
      <Header className="flex items-center gap-2 px-2.5 py-1.5 bg-elevated border-b border-line shrink-0" />
      <Canvas className="flex-1 min-h-0 relative" />
    </div>
  ))}
</div>
```

### 5.3 Strategy three-pane

```jsx
<div className="grid grid-cols-[210px_1fr_420px] h-full">
  <aside className="flex flex-col bg-panel border-r border-line" />            {/* palette */}
  <div className="relative overflow-hidden bg-base
       [background-image:radial-gradient(theme(borderColor.line)_1px,transparent_1px)]
       [background-size:22px_22px]" />                                          {/* canvas */}
  <div className="flex flex-col min-h-0 bg-panel border-l border-line">
    <RiskControls className="shrink-0 border-b border-line p-3.5" />
    <CodePane className="flex-1 overflow-auto bg-void" />                       {/* split-screen */}
  </div>
</div>
```

### 5.4 Backtest dashboard

```jsx
<div className="flex flex-col h-full overflow-y-auto bg-base">
  <header className="sticky top-0 z-[6] flex items-center gap-3.5 px-4 py-3 bg-panel border-b border-line" />
  <div className="grid grid-cols-4 gap-px bg-line border-b border-line">
    {kpis.map(k => <div className="bg-panel px-4 py-3 flex flex-col gap-1.5" />)}
  </div>
  <div className="grid grid-cols-[1fr_340px] gap-3.5 p-4">
    <div className="flex flex-col gap-3 min-w-0" />  {/* equity + trade log */}
    <div className="flex flex-col gap-3" />          {/* heatmap + dist + friction */}
  </div>
</div>
```

### 5.5 Editable filter chip

```jsx
<span className="inline-flex items-center gap-1.5 h-6 pl-2.5 pr-1 rounded-sm
  bg-input border border-line2 font-mono text-[11.5px] text-tx whitespace-nowrap">
  <i className="w-[5px] h-[5px] rounded-full" style={{background:catColor}} />
  <span className="text-txlo">{field.label}</span>
  <button className="px-1 font-semibold text-txhi hover:bg-active rounded-[3px]">{CMP[cmp]}</button>
  {editing
    ? <input className="w-11 text-center bg-active border border-ac/40 text-ac-hi rounded-[3px] outline-none" />
    : <span className="text-ac-hi px-0.5 rounded hover:bg-ac/[.07] cursor-text">{value}{unit}</span>}
  <button className="grid place-items-center w-4 h-4 rounded-[3px] text-txdim hover:bg-bear/[.12] hover:text-bear">✕</button>
</span>
```

---

## 6. Interaction & Motion Tokens

| Pattern | Spec |
|---------|------|
| Control transition | `transition: all .12s` (color/bg/border) |
| Row hover | `.08s` background |
| Live tick flash | `flash-up/flash-down` keyframe `.55s ease-out`, tint→transparent |
| Status/feed pulse | `pulse 2s ease-in-out infinite`, opacity 1→.35 |
| Ticker scroll | `80s linear infinite`, pausable via parent data-attr |
| Tooltip | opacity `.12s`, no movement chase |
| Node drag | direct 1:1, no easing (pointer-true) |

---

## 7. Semantic Color Usage Rules

1. **Never** use violet for gain/loss; **never** use green/red for selection/focus.
2. Saturated fills (`bull/bear/warn/ac` solid) only on: live-tick flash, KPI values, badges, active pills, execution markers, last-price tags. Everything structural is the neutral ramp.
3. Badges/fills use the 12% `-bg` tint; text uses the `-hi` variant for contrast on dark.
4. RSI gauge & heatmap encode magnitude via **opacity**, not hue shifts, to stay colorblind-parseable alongside the fixed green/red poles.
5. Amber is exclusively caution/cost (latency >16ms, exposure ceiling, slippage, friction).

---

## 8. Accessibility & Performance Notes

- **Contrast:** primary text `#e9ecf2` on `#0a0c10` ≈ 15:1; secondary `#aeb6c4` ≈ 8:1. Avoid `--tx-dim` for essential copy.
- **Tabular numerals everywhere** prevent column jitter during live ticks.
- **Hit targets:** rail/buttons ≥ 40px; inline chip controls 16–24px are pointer-secondary with keyboard equivalents.
- **Render budget:** SVG charts use `vector-effect:non-scaling-stroke` + `preserveAspectRatio="none"` to scale without re-layout; live updates are push-based (`LiveBus` pub/sub, 1.4s cadence) rather than per-component polling.
- **Scroll ownership:** exactly one scroll container per region; shell never scrolls.
- **Focus:** every interactive element carries a violet focus ring (`--ac-line`); never rely on hover alone.
- **Reduced motion:** gate ticker scroll, pulse, and tick-flash behind `@media (prefers-reduced-motion: reduce)`.

---

*Reference implementation: `Axiom Terminal.html` (+ `styles.css`, `modules.css`, `data.jsx`, `icons.jsx`, `shell.jsx`, `screener.jsx`, `charts.jsx`, `strategy.jsx`, `backtest.jsx`). Tokens in this document are the single source of truth; the Tailwind config in §2.8 is generated from the CSS variables in `styles.css`.*
