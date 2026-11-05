import { defineConfig } from '@argo-video/cli';
import { lfm3Engine } from './lfm3-engine.mjs';

export default defineConfig({
  baseURL: 'http://127.0.0.1:8000',
  demosDir: 'demos',
  outputDir: 'videos',
  tts: {
    engine: lfm3Engine({ baseUrl: 'http://127.0.0.1:8082', voice: 'female', lang: 'en' }),
    defaultVoice: 'female',
    defaultSpeed: 1.0,
  },
  video: {
    width: 1920,
    height: 1080,
    fps: 30,
    browser: 'chromium',
    browserChannel: 'chrome',
    captureMode: 'jpeg-stitch',
    jpegQuality: 95,
    deviceScaleFactor: 1,
    cursorHighlight: { mode: 'click' },
    contextOptions: { colorScheme: 'light' },
  },
  export: {
    preset: 'slow',
    crf: 18,
    encoder: 'cpu',
    audio: { loudnorm: true },
    sharpen: { strength: 0.35 },
  },
  overlays: {
    autoBackground: true,
  },
});
