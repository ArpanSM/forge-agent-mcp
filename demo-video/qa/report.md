# QA: agent-mcp.mp4

1920x1080 @ 30.000 fps, 86.83 s, audio: True

| level | check | detail |
|---|---|---|
| PASS | frame 0 | mean 199.4, contrast 63.4; still confirm it's a finished composition |
| PASS | single-frame flashes | none |
| WARN | pops | small spikes at [4.8, 4.933, 5.0, 5.067, 5.267, 5.333, 5.467, 5.667, 5.867, 17.633, 17.833, 17.933, 51.367, 62.567, 79.733, 81.0]: intended hits, or glitches/stray transitions? |
| INFO | detected cuts | [4.167, 13.967, 24.333, 36.233, 42.367, 67.2, 73.833] |
| WARN | frozen stretch | 12.27-13.93 s (1.67 s): designed hold or dead air? |
| WARN | frozen stretch | 22.73-24.33 s (1.6 s): designed hold or dead air? |
| WARN | frozen stretch | 34.83-36.23 s (1.4 s): designed hold or dead air? |
| WARN | frozen stretch | 50.0-51.37 s (1.37 s): designed hold or dead air? |
| WARN | frozen stretch | 60.87-62.57 s (1.7 s): designed hold or dead air? |
| WARN | frozen stretch | 64.0-65.63 s (1.63 s): designed hold or dead air? |
| WARN | frozen stretch | 72.83-73.83 s (1.0 s): designed hold or dead air? |
| PASS | loudness | -14.5 LUFS (target -14.0 +/-1), true peak -1.4 dBTP (<= -1.0) |
| INFO | loudest moment | loudest 100 ms at 28.6 s (-7.6 dBFS); is this the payoff? |
| PASS | silence before peak | silences >= 0.4 s below -30.0 dBFS: [{'from': 5.2, 'to': 5.85}, {'from': 7.15, 'to': 7.6}, {'from': 11.55, 'to': 14.05}, {'from': 18.4, 'to': 19.05}, {'from': 23.8, 'to': 24.75}, {'from': 27.85, 'to': 28.45}, {'from': 32.1, 'to': 32.65}, {'from': 35.85, 'to': 36.75}] |

Sheets: qa/beats_sheet.jpg, qa/phone_sheet.jpg, qa/hook_sheet.jpg
Look at all three: numbers catch broken, not bad. The hook sheet is the muted test.
