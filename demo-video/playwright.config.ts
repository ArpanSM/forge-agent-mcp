import { defineConfig } from '@playwright/test';
import config from './argo.config.mjs';

const scale = Math.max(1, Math.round(config.video?.deviceScaleFactor ?? 1));
const width = config.video?.width ?? 1920;
const height = config.video?.height ?? 1080;

// Recording is driven by narration.startRecording(page) in the demo
// (page.screencast.start under the hood) — no Playwright recordVideo here.
export default defineConfig({
  preserveOutput: 'always',
  timeout: 180_000,
  projects: [
    {
      name: 'demos',
      testDir: 'demos',
      testMatch: '**/*.demo.ts',
      use: {
        browserName: config.video?.browser ?? 'chromium',
        channel: config.video?.browserChannel ?? 'chrome',
        baseURL: process.env.BASE_URL || config.baseURL || 'http://127.0.0.1:8000',
        viewport: { width, height },
        deviceScaleFactor: scale,
        colorScheme: 'light',
        video: 'off',
      },
    },
  ],
});
