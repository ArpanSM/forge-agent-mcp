# Agent MCP narrated demo

A 1920×1080, 30 fps narrated walkthrough of the Forge reviewer console: the
16-case synthetic queue, Ridgeline onboarding with cited fields, a human
approval that commits through the MCP gateway, the audit chain, and the
Northgate invoice hold.

## Deliverables

| File | What it is |
|---|---|
| `videos/agent-mcp.mp4` | 86.8 s master, H.264 + AAC, 1920×1080 at 30 fps |
| `videos/agent-mcp.srt` | SubRip narration |
| `videos/agent-mcp.vtt` | WebVTT narration |
| `videos/agent-mcp-poster.png` | 1920×1080 poster frame (end card) |
| `qa/report.md`, `qa/report.json` | Video Director QA report |
| `qa/beats_sheet.jpg`, `qa/phone_sheet.jpg`, `qa/hook_sheet.jpg` | Contact sheets |
| `qa/notes.md` | Reviewer notes on the QA warnings |

Source: `demos/agent-mcp.demo.ts`, `demos/agent-mcp.scenes.json`,
`argo.config.mjs`, `playwright.config.ts`, `lfm3-engine.mjs`.

## 30-second launch film

`huashu-film/` is the companion Huashu Motion cut: a 30-second, 60 fps, music-led
launch film about the approval gate. It uses real frames from the Argo master
and the console's own palette. See `huashu-film/README.md` for the direction,
material provenance and reproduce commands.

## Prerequisites

- Python 3.12, with the repository installed (`make install`).
- Node 20+, `npm install` in this directory.
- Google Chrome 156 (Playwright uses `browserChannel: 'chrome'`; no Chromium
  download is required).
- ffmpeg/ffprobe on `PATH`.
- LFM3 audio on `127.0.0.1:8082`:

  ```bash
  lfm3 audio          # load LFM3-Audio-2B; health at /health
  lfm3 status
  ```

## Reproduce

Start a clean console (it resets `data/console-demo/` and leaves both demo
cases waiting for a human decision):

```bash
cd /home/arpan/Forge/forge-agent-mcp
make console
```

In a second shell:

```bash
cd /home/arpan/Forge/forge-agent-mcp/demo-video
PATH=/tmp/opencode/bin:$PATH npx argo pipeline agent-mcp
node scripts/normalize-subtitles.mjs \
  videos/agent-mcp.srt videos/agent-mcp.vtt
```

The pipeline runs TTS → record → align → export. The LFM3 engine converts the
24 kHz PCM16 response into the 32-bit float WAV Argo expects, and applies the
small `atempo` speed adjustment from the scene manifest with ffmpeg.

To re-export an existing recording after tuning camera moves, without
re-recording:

```bash
PATH=/tmp/opencode/bin:$PATH node scripts/export-recording.mjs agent-mcp
```

Argo's standalone `argo export` does not load `.timing.camera-moves.json`; this
helper does, then applies the same encode settings.

## QA

```bash
cd /home/arpan/Forge/forge-agent-mcp/demo-video
python3 .claude/skills/video-director/scripts/qa_video.py \
  videos/agent-mcp.mp4 --out qa/
```

The delivered QA run:

```bash
PATH=/tmp/opencode/bin:$PATH python3   .claude/skills/video-director/scripts/qa_video.py   videos/agent-mcp.mp4 --out qa/   --times 0,13.9,24.6,36.6,51.7,62.6,74.3 --offset 0.8
```

It passes frame 0, flashes, loudness and silence-before-peak. The remaining
warnings are reviewed in `qa/notes.md`: seven 1.0–1.7 s scene-tail holds and
intentional UI/cut transitions. Loudness is −14.5 LUFS, true peak −1.4 dBTP.

## Notes

- The corpus is synthetic. The end card carries
  “Synthetic corpus · local demo · not production”.
- The console is the only component that mints the approval token; the agent
  never holds the signing key.
- No burned-in captions: narration lives in the SRT/VTT files. Compact
  in-page strips label the evidence, the human decision and the hold.
- `make console` resets the demo data on every start. Restart it before a
  fresh take.
- `lfm3 off` stops the audio model and frees VRAM when finished.
