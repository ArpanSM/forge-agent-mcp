import { spawnSync } from 'node:child_process';

const DEFAULTS = {
  baseUrl: 'http://127.0.0.1:8082',
  voice: 'female',
  lang: 'en',
};

function parseWav(buf) {
  if (buf.toString('ascii', 0, 4) !== 'RIFF' || buf.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error('LFM3 did not return a WAV');
  }

  let offset = 12;
  let fmt = null;
  let data = null;

  while (offset + 8 <= buf.length) {
    const id = buf.toString('ascii', offset, offset + 4);
    const size = buf.readUInt32LE(offset + 4);
    const body = offset + 8;

    if (id === 'fmt ') {
      fmt = {
        format: buf.readUInt16LE(body),
        channels: buf.readUInt16LE(body + 2),
        sampleRate: buf.readUInt32LE(body + 4),
        bits: buf.readUInt16LE(body + 14),
      };
    } else if (id === 'data') {
      data = buf.subarray(body, body + size);
    }

    offset = body + size + (size % 2);
  }

  if (!fmt || !data) throw new Error('LFM3 WAV missing fmt/data chunk');
  return { fmt, data };
}

/**
 * Argo's pipeline reads 32-bit float WAV samples. LFM3's /tts endpoint returns
 * 24 kHz mono PCM16, so convert the payload before handing it to Argo.
 */
function applyTempo(samples, sampleRate, speed) {
  if (!speed || Math.abs(speed - 1) < 0.001) return samples;

  const input = Buffer.alloc(samples.length * 4);
  for (let i = 0; i < samples.length; i++) input.writeFloatLE(samples[i], i * 4);

  const result = spawnSync(
    'ffmpeg',
    [
      '-hide_banner',
      '-loglevel',
      'error',
      '-f',
      'f32le',
      '-ar',
      String(sampleRate),
      '-ac',
      '1',
      '-i',
      'pipe:0',
      '-filter:a',
      `atempo=${speed}`,
      '-f',
      'f32le',
      'pipe:1',
    ],
    { input, maxBuffer: 128 * 1024 * 1024 },
  );

  if (result.error) {
    throw new Error(`ffmpeg tempo failed: ${result.error.message}`);
  }
  if (result.status !== 0) {
    throw new Error(`ffmpeg tempo failed: ${result.stderr?.toString() || result.status}`);
  }

  const out = new Float32Array(result.stdout.length / 4);
  for (let i = 0; i < out.length; i++) out[i] = result.stdout.readFloatLE(i * 4);
  return out;
}

function toFloat32Wav(buf, speed = 1) {
  const { fmt, data } = parseWav(buf);
  let samples;

  if (fmt.format === 3 && fmt.bits === 32) {
    samples = new Float32Array(data.length / 4);
    for (let i = 0; i < samples.length; i++) {
      samples[i] = data.readFloatLE(i * 4);
    }
  } else if (fmt.format === 1 && fmt.bits === 16) {
    samples = new Float32Array(data.length / 2);
    for (let i = 0; i < samples.length; i++) {
      samples[i] = data.readInt16LE(i * 2) / 32768;
    }
  } else {
    throw new Error(`Unsupported LFM3 WAV format=${fmt.format} bits=${fmt.bits}`);
  }

  const sampleRate = fmt.sampleRate;
  const channels = fmt.channels;
  samples = applyTempo(samples, sampleRate, speed);
  const header = Buffer.alloc(44);

  header.write('RIFF', 0, 'ascii');
  header.writeUInt32LE(36 + samples.length * 4, 4);
  header.write('WAVE', 8, 'ascii');
  header.write('fmt ', 12, 'ascii');
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(3, 20);
  header.writeUInt16LE(channels, 22);
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(sampleRate * channels * 4, 28);
  header.writeUInt16LE(channels * 4, 32);
  header.writeUInt16LE(32, 34);
  header.write('data', 36, 'ascii');
  header.writeUInt32LE(samples.length * 4, 40);

  const out = Buffer.concat([header, Buffer.alloc(samples.length * 4)]);
  for (let i = 0; i < samples.length; i++) {
    out.writeFloatLE(samples[i], 44 + i * 4);
  }
  return out;
}

export function lfm3Engine(opts = {}) {
  const cfg = { ...DEFAULTS, ...opts };

  return {
    describe: () => ({ engine: 'lfm3', model: 'lfm3-audio-2b' }),
    async generate(text, options = {}) {
      if (!text?.trim()) throw new Error('TTS text must not be empty');

      const res = await fetch(`${cfg.baseUrl}/tts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          input: text,
          voice: options.voice || cfg.voice,
          lang: options.lang || cfg.lang,
        }),
      });

      if (!res.ok) {
        throw new Error(`LFM3 TTS failed: ${res.status} ${await res.text()}`);
      }

      return toFloat32Wav(
        Buffer.from(await res.arrayBuffer()),
        options.speed || cfg.speed || 1,
      );
    },
  };
}
