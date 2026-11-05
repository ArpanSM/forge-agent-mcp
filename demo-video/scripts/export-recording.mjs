import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import config from '../argo.config.mjs';
import { exportVideo } from '../node_modules/@argo-video/cli/dist/export.js';
import { shiftCameraMoves } from '../node_modules/@argo-video/cli/dist/camera-move.js';
import { getVideoDurationMs } from '../node_modules/@argo-video/cli/dist/media.js';
import {
  buildPlacementsFromTimingAndDurations,
  computeHeadTrimMs,
  shiftPlacements,
} from '../node_modules/@argo-video/cli/dist/timeline.js';

// Re-export the existing recording without re-recording. Argo's standalone
// `argo export` does not pass recorded camera moves to the encoder; the
// pipeline does. This keeps a clean take and applies its zoom moves.

const demo = process.argv[2] ?? 'agent-mcp';
const argoDir = '.argo';
const demoDir = join(argoDir, demo);
const timing = JSON.parse(readFileSync(join(demoDir, '.timing.json'), 'utf8'));
const sceneDurations = JSON.parse(
  readFileSync(join(demoDir, '.scene-durations.json'), 'utf8'),
);
const videoPath = join(demoDir, 'video.mp4');
const totalDurationMs = getVideoDurationMs(videoPath);
const headTrimMs = computeHeadTrimMs(timing);
const placements = shiftPlacements(
  buildPlacementsFromTimingAndDurations(timing, sceneDurations, totalDurationMs),
  headTrimMs,
);
const cameraMoves = shiftCameraMoves(
  JSON.parse(readFileSync(join(demoDir, '.timing.camera-moves.json'), 'utf8')),
  headTrimMs,
);

await exportVideo({
  demoName: demo,
  argoDir,
  outputDir: config.outputDir,
  preset: config.export.preset,
  crf: config.export.crf,
  fps: config.video.fps,
  outputWidth: config.video.width,
  outputHeight: config.video.height,
  deviceScaleFactor: config.video.deviceScaleFactor,
  placements,
  totalDurationMs: totalDurationMs - headTrimMs,
  cameraMoves,
  loudnorm: config.export.audio?.loudnorm,
  sharpen: config.export.sharpen,
  encoder: config.export.encoder,
  encoderDefault: 'cpu',
});
