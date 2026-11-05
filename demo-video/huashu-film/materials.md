# Materials

The film is made from the real synthetic console, not invented UI.

## Used in the film

All four are frames from the narrated Argo master
`demo-video/videos/agent-mcp.mp4` (1920×1080, 30 fps), captured from the real
`make console` run:

- `materials/local-forge-agent-mcp/queue-close.png` — 0:08, the 16-case review queue with
  the two pending approvals visible (Ridgeline and Northgate).
- `materials/local-forge-agent-mcp/ridgeline-evidence.png` — 0:19, Ridgeline Haulage's
  documents, 45 extracted fields and the (still unapproved) recommendation panel.
- `materials/local-forge-agent-mcp/ridgeline-committed.png` — 0:44, the committed
  decision panel: one transaction, Approved, `audit verified`, decision
  `dec_9aa10f8555a6`.
- `materials/local-forge-agent-mcp/audit-chain.png` — 0:54, the append-only audit trail
  with `create_decision` / `write_committed` and `chain verified`.

The Argo master itself is not copied into the project (55 MB); the four frames above are
the only footage used. The original stills in `docs/screenshots/` and the Argo master are
the provenance of every string and number in the film.

## Palette and type

- Cream `#f7f3e9`, dark green `#173f38`, navy `#142235`, amber `#b45309` — the existing
  end-card palette and the console's own CSS variables. The UI mint/green
  `#e9f2ec` / `#2f7d63` is used only for the real recommendation/audit states.
- Type: Inter (headline/UI) and Roboto Condensed (labels), installed into
  `assets/fonts/`.
- No logo file exists for Forge Agent + MCP; the film therefore uses typography, not a
  redrawn mark. The small `N` square in the screenshots belongs to the synthetic tenant
  Northwind Freight Capital and is shown only as part of the real capture.

## What is code-built

The citation rows in the macro shot, the gate/recommendation/decision panels, the token
chip and the write receipt are HTML/CSS rebuilds traced from the frames above at
1920×1080, with the same text, figures and states. They are not new product UI; they are
the same moments made movable for the film.
