# Forge Agent + MCP — The Gate

A 30-second launch film for the synthetic freight-underwriting agent. Music and effects
only; no narration. The narrated Argo walkthrough remains the product tour.

**One idea:** the agent can read all 16 cases, cite every field and recommend approval —
and still cannot pass the last inch. A human decision mints the token; the audited write is
the payoff.

## Deliverables

| File | What it is |
|---|---|
| `out/forge-agent-mcp-launch.mp4` | 30.00 s, 1920×1080, 60 fps, H.264 + AAC |
| `poster.png` | 1920×1080 end-card poster (also the player's first-frame poster is the real queue) |
| `qa/` | `om check` and `om qa` output, hook frame, contact sheet |

## The film

```
0.0–4.0   real 16-case queue; "16 cases / 2 need a human"; punch into Ridgeline
4.0–5.5   real Ridgeline extraction frame; 45 fields; scan for citations
5.5–8.0   macro citation rows: Legal name / MC number / EIN, page + bbox + quote
8.0–12.0  recommendation reaches the amber gate; "It can recommend. It cannot approve itself."
12.0–14.0 stop: the token is required; the human cursor enters
14.0–16.0 human click; HUMAN DECISION lights; gate lifts; token flies; one transaction
16.0–18.0 committed decision: one transaction, audit verified, decision id
18.0–21.0 audit chain: every write has a principal; chain verified
21.0–24.0 proof burst: 100% hard-stop recall · 0 duplicate writes · 0 bypasses · 100% audit completeness
24.0–30.0 end card: the claim, stats, URL, caveats, Huashu signature
```

## Direction

**A · The Gate** was chosen from three internal directions (see `directions.md` and the
three key frames in `directions/`). It is the only one whose central object is the rule
itself: the recommendation hits a boundary it cannot cross. B · Every Value Has a Page was
the best pure evidence frame, and its macro citation language is reused inside A. C · The
Packet was the depth direction; it made the film about documents rather than the gate.

Look: the real console as the world; cream paper, dark-green ink, one amber gate. Type is
Inter with Roboto Condensed labels. The film is a hybrid: a continuous spine through the
real UI, broken by a proof burst at 21–24 s.

## Material

The four real captures are frames from `../videos/agent-mcp.mp4` (the narrated Argo master):
the queue at 0:08, Ridgeline's evidence at 0:19, the committed decision at 0:44, and the
audit chain at 0:54. They are listed with provenance in `materials.md`. The citation rows,
gate panels and receipt are code rebuilds traced from those captures, using the console's
real words and numbers. No invented UI, no redrawn logo.

The corpus is synthetic. The film never claims production readiness, real data, compliance
or a hosted service.

## Sound

A written `om-score` at 120 BPM in A minor: 808 kit, synbass, marimba, piano and strings.
The stop at 12 s is a tape stop; the drop at 14 s lands on the human click; the end card is a
button with a ring. Sound effects are marked on the landings. No voice.

## QA

```text
om check: 0 errors, 0 warnings
om qa:    no single-frame pops; no failed checks; 4 hard cuts reported; 30.00 s
audio:    -14.1 LUFS integrated, -1.3 dBTP true peak (AAC)
```

`om qa` still lists the planned reading holds (the evidence frame, the gate, the silence
before the human decision, the committed decision, and the end-card interval between the two
light sweeps) as "still for ≥1 s"; they are deliberate holds, not dead frames. The longest
single flagged hold is the 1.75 s committed-decision read; the 12–14 s stop is broken by the
entering cursor and the reverse swell.

## Reproduce

From `/home/arpan/Forge/forge-agent-mcp/demo-video`:

```sh
export PATH=/tmp/opencode/bin:$PATH

npx om learn launch rhythm sound
npx om grab /home/arpan/Forge/forge-agent-mcp huashu-film
npx om check huashu-film
npx om sound huashu-film
npx om still huashu-film --at 0,1.45,4.4,6.2,10.3,14.2,16.9,19.6,21.5,23.2,27.3,29.5
npx om render huashu-film --fps 60 --workers 8 --blur 4
npx om qa huashu-film
```

`--blur 8` at 60 fps is rejected by Huashu (480 sub-frames/s over its 240 limit); `--blur 4`
is the maximum at this frame rate. The final master was rendered with motion blur.

## Notes

- The Huashu signature ("Made with huashu-motion") is on in the free top-right corner of
  the end card. Switch it off with `data-om-signature="off"` on the root if the parent
  prefers a client-clean master.
- No upload, commit or push was performed.
