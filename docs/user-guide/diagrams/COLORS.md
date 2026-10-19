# Diagram colours

Colour codes for the SVG diagrams in this folder. One colour means one thing in every diagram.

## Identities

| Identity | Accent (bar, border, dot) | Text | Tint (box fill) |
| --- | --- | --- | --- |
| `deep_reasoner` | `#4f46e5` indigo | `#4f46e5` | `#eef2ff` |
| `fast_worker` | `#d97706` amber | `#b45309` | `#fffbeb` |
| `arbiter` | `#be185d` pink | `#be185d` | `#fdf2f8` |
| `e2e_specifier`, `e2e_verifier` | `#0891b2` cyan | `#0e7490` | white |
| The driver (Claude Code, not an identity) | `#D97757` Claude orange | `#b4532f` | `#fdf2ee`, soft border `#f5cdbf` |

- The Worker CLI lane in `handoff-lifecycle.svg` uses teal, so it stands apart from the driver lane above it: border `#0d9488`, label `#0f766e`, lane tint `rgba(13,148,136,0.05)`, lane border `#99f6e4`, label column `rgba(13,148,136,0.10)`. The job box and the fix-round box in that lane use the same teal border.
- In the other diagrams, a generic "worker" (any identity in a job) still uses the `fast_worker` amber. Example: "the worker" in `review-gates.svg` and `handoff-packet-anatomy.svg`.
- The driver uses Claude orange because the driver is always Claude Code. It matches the Claude icon.
- Driver lane tint: `rgba(217,119,87,0.06)`, label column `rgba(217,119,87,0.10)`.

## Worker CLIs (backends)

| Backend | Border | Text | Fill | Icon fill |
| --- | --- | --- | --- | --- |
| `codex` | `#334155` | `#334155` | `#f1f5f9` | `#0f172a` |
| `claude` | `#c2410c` | `#c2410c` | `#fff7ed` | `#D97757` |
| `copilot` | `#2563eb` | `#1d4ed8` | `#eff6ff` | `#0f172a` |
| `cursor` | `#15803d` | `#15803d` | `#f0fdf4` | `#0f172a` |
| `antigravity` | `#a21caf` | `#a21caf` | `#fdf4ff` | `#0f172a` |

- Icons are the same marks as `assets/setup-ui.html`, as `<symbol id="ic-<backend>">` in each SVG's `<defs>`.

## Other meanings

| Meaning | Colour |
| --- | --- |
| You (the user), general accent (setup steps, config file, goal file, hotspots) | `#7c3aed` violet, text `#6d28d9`, tint `#f5f3ff` |
| Pass, done, agreed | `#059669`, text `#047857`, tint `#ecfdf5` |
| Fail, refusal, rework or escalation arrow | `#e11d48`, text `#be123c`, tint `#fff1f2`, border `#fda4af` |
| Running, monitor | `#2563eb`, text `#1d4ed8`, tint `#eff6ff` |
| Cancelled, blocked, neutral | `#94a3b8`, text `#475569` |
| Packet label | `#b45309` |
| Flow arrow | `#64748b` |
| State read or write (dashed) | `#94a3b8` |
| Wrap up, `.handoff/` lane | `#475569` |

## Base

- Ink `#0f172a`, body text `#475569`, small text `#64748b`.
- Borders `#e2e8f0`, page `#f8fafc`, grid `#e6ebf2`.

## Rules

- Set a text colour with `style="fill:#..."`, not with a `fill` attribute. Each SVG's stylesheet sets `text { fill }`, and any CSS rule overrides a `fill` attribute.
- Known overlaps:
  - `fast_worker` text and packet labels share `#b45309`.
  - Driver orange and `fast_worker` amber are near each other.
  - `copilot` blue and the running/monitor blue are the same.
  - Keep each one next to its label so the meaning stays clear.
