# SYSTEM-REPORT — 5 route(s), 2026-09-27

The system as **painted**, not as documented. A token in the stylesheet that never renders is
not part of the system; a one-off inline style is. Read this against `design/DESIGN.md` — every
number below that DESIGN.md does not account for is drift.

**Look at `components.png`** — one tile per distinct rendered control variant. Two tiles that look the same to you but appear separately are the drift.

## Controls

| kind | distinct variants | budget | total instances |
|---|---|---|---|
| link | **3** | 4 | 11 |
| button | **2** | 4 | 102 |
| input | **2** | 4 | 2 |
| select | **2** | 4 | 2 |

### link
- ×4 on 1 route(s) — "商单台账" — `rgba(0, 0, 0, 0) | rgba(29, 39, 51, 0.72) | rgba(29, 39, 51, 0.72) rgba(29, 39, 51, 0.72) rgba(29, 39, 51, 0.72) rgba(0, 0, 0, 0) | 0b | 6r | 14/400 | 9x12 | noshadow`
- ×1 on 1 route(s) — "全部" — `rgb(15, 76, 92) | rgb(255, 255, 255) | rgb(15, 76, 92) | 1b | 20r | 13/400 | 5x12 | noshadow`
- ×6 on 1 route(s) — "需求沟通" — `rgb(255, 255, 255) | rgba(29, 39, 51, 0.72) | rgb(212, 208, 199) | 1b | 20r | 13/400 | 5x12 | noshadow`
- *states (not counted as variants): current ×5*

### button
- ×2 on 1 route(s) — "新对话" — `rgb(15, 76, 92) | rgb(255, 255, 255) | rgb(255, 255, 255) | 0b | 6r | 14/600 | 9x9 | noshadow`
- ×100 on 1 route(s) — "档期" — `rgba(0, 0, 0, 0) | rgb(15, 76, 92) | rgb(15, 76, 92) | 0b | 4r | 13/600 | 3x6 | noshadow`
- *states (not counted as variants): disabled ×1*

### input
- ×1 on 1 route(s) — "textarea" — `rgb(247, 246, 242) | rgb(29, 39, 51) | rgb(212, 208, 199) | 1b | 10r | 14/400 | 10x12 | noshadow`
- ×1 on 1 route(s) — "text" — `rgb(255, 255, 255) | rgb(29, 39, 51) | rgb(212, 208, 199) | 1b | 6r | 13/400 | 7x11 | noshadow`

### select
- ×1 on 1 route(s) — "全部层级
头部
腰部
尾部" — `rgb(255, 255, 255) | rgb(29, 39, 51) | rgb(212, 208, 199) | 1b | 6r | 13/400 | 7x11 | noshadow`
- ×1 on 1 route(s) — "达人库" — `rgb(255, 255, 255) | rgb(29, 39, 51) | rgb(212, 208, 199) | 1b | 6r | 13/400 | 8x11 | noshadow`

> States — disabled, current, and controls inside a row carrying a `data-state` — are
> excluded from the variant budget. A disabled button paints differently on purpose; a
> product that has no disabled state at all should not score better than one that does.

## Tokens as rendered

**Colour** (20 distinct)
- `rgb(29, 39, 51)` — 602× on 1 route(s)
- `rgba(29, 39, 51, 0.72)` — 204× on 1 route(s)
- `rgba(29, 39, 51, 0.55)` — 200× on 1 route(s)
- `rgb(15, 76, 92)` — 100× on 1 route(s)
- `rgb(226, 237, 239)` — 58× on 1 route(s)
- `rgb(10, 58, 71)` — 58× on 1 route(s)
- `rgb(251, 250, 247)` — 50× on 1 route(s)
- `rgb(230, 241, 234)` — 42× on 1 route(s)
- `rgb(47, 125, 79)` — 42× on 1 route(s)
- `rgb(250, 249, 246)` — 40× on 1 route(s)
- …and 10 more

**Type step** (10 distinct)
- `13px/400/Avenir Next` — 699× on 1 route(s)
- `13px/600/Avenir Next` — 200× on 1 route(s)
- `12px/600/Avenir Next` — 111× on 1 route(s)
- `12px/400/SF Mono` — 90× on 1 route(s)
- `12px/400/Avenir Next` — 90× on 1 route(s)
- `14px/400/Avenir Next` — 4× on 1 route(s)
- `15px/700/Avenir Next` — 2× on 1 route(s)
- `14px/600/Avenir Next` — 2× on 1 route(s)
- `11px/400/Avenir Next` — 2× on 1 route(s)
- `18px/700/Avenir Next` — 1× on 1 route(s)

**Radius** (6 distinct)
- `4px` — 102× on 1 route(s)
- `20px` — 100× on 1 route(s)
- `50px` — 90× on 1 route(s)
- `6px` — 54× on 1 route(s)
- `10px` — 1× on 1 route(s)
- `14px` — 1× on 1 route(s)

**Shadow** (1 distinct)
- `rgba(29, 39, 51, 0.06) 0px 1px 2px 0px` — 1× on 1 route(s)

## Per route

| route | landmarks | h1 | focusable | console errors |
|---|---|---|---|---|
| http://localhost:5173/#/ | nav,main,aside | 0 | 8 | 0 |
| http://localhost:5173/#/deals | nav,main,aside | 0 | 5 | 0 |
| http://localhost:5173/#/kols | nav,main,aside | 0 | 107 | 0 |
| http://localhost:5173/#/proposals | nav,main,aside | 0 | 16 | 0 |
| http://localhost:5173/#/import | nav,main,aside | 0 | 7 | 0 |

## WARN
- link variant used exactly once ("全部") — either promote it into the system or delete it: rgb(15, 76, 92) | rgb(255, 255, 255) | rgb(15, 76, 92) | 1b | 20r | 13/400 | 5x12 | noshadow
- input variant used exactly once ("textarea") — either promote it into the system or delete it: rgb(247, 246, 242) | rgb(29, 39, 51) | rgb(212, 208, 199) | 1b | 10r | 14/400 | 10x12 | noshadow
- input variant used exactly once ("text") — either promote it into the system or delete it: rgb(255, 255, 255) | rgb(29, 39, 51) | rgb(212, 208, 199) | 1b | 6r | 13/400 | 7x11 | noshadow
- select variant used exactly once ("全部层级
头部
腰部
尾部") — either promote it into the system or delete it: rgb(255, 255, 255) | rgb(29, 39, 51) | rgb(212, 208, 199) | 1b | 6r | 13/400 | 7x11 | noshadow
- select variant used exactly once ("达人库") — either promote it into the system or delete it: rgb(255, 255, 255) | rgb(29, 39, 51) | rgb(212, 208, 199) | 1b | 6r | 13/400 | 8x11 | noshadow
