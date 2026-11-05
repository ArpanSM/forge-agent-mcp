# QA notes

`report.md` and `report.json` are the unedited output of
`.claude/skills/video-director/scripts/qa_video.py` on the delivered MP4.

Result: **0 FAIL**. Loudness passes at −14.5 LUFS with a −1.4 dBTP true peak.
Frame 0 is a finished queue composition, not black; no single-frame flashes.

The remaining WARN lines are reviewed and intentional:

- **Frozen stretches, 1.0–1.7 s.** Each one is the tail of a scene after its
  slow camera move has finished, while the narration is still completing. The
  camera push is continuous during the scene (verified with frame diffs at
  15/18/20/22 s, 25/27/30/33 s, 38/42/46 s, 52/56/60 s and 76/80/84 s). There
  are no holds over 2 s; the previous 5–12 s holds were removed.
- **Pops at 4.8–5.9 s.** The queue's hook band fades out and the cursor moves
  to the Ridgeline row; these are UI paint changes, not audio clicks.
- **Pops at 17.6–17.9 s, 51.4 s, 62.6 s, 79.7 s, 81.0 s.** Scene cuts,
  camera-move starts, and the end-card rule/stat entrances. Measured true peak is −1.4 dBTP; the decoded waveform does not clip.

The 12.1 s final card is narration-led: the close line runs 12.08 s. The card
has a continuous slow push-in, so it is a designed end-card hold rather than a
dead frame.
