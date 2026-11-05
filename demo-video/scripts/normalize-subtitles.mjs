import { readFileSync, writeFileSync } from 'node:fs';

// Argo 0.42.1 writes fractional milliseconds into its cue timestamps, e.g.
// "00:00:11,706.7916666666661". Players reject that. Normalise to whole
// milliseconds while preserving cue order and text.

const TIME = /(\d{2}):(\d{2}):(\d{2})[.,](\d+(?:\.\d+)?)/g;

function toMs(hours, minutes, seconds, fraction) {
  const ms = Number(String(fraction).split('.')[0].padStart(3, '0').slice(0, 3));
  return ((Number(hours) * 60 + Number(minutes)) * 60 + Number(seconds)) * 1000 + ms;
}

function stamp(ms, separator) {
  const whole = Math.max(0, Math.round(ms));
  const h = Math.floor(whole / 3_600_000);
  const m = Math.floor((whole % 3_600_000) / 60_000);
  const s = Math.floor((whole % 60_000) / 1000);
  const r = whole % 1000;
  const pad = (n, size = 2) => String(n).padStart(size, '0');
  return `${pad(h)}:${pad(m)}:${pad(s)}${separator}${pad(r, 3)}`;
}

function fixTimingLine(line, separator) {
  const times = [...line.matchAll(TIME)].map((m) => toMs(m[1], m[2], m[3], m[4]));
  if (times.length < 2) return line;
  // The recorder can stamp the first mark at 1 ms; make the opening cue start at 0.
  if (times[0] < 2) times[0] = 0;
  return `${stamp(times[0], separator)} --> ${stamp(times[1], separator)}`;
}

function fixSrt(text) {
  return text
    .split(/\r?\n/)
    .map((line) => (line.includes('-->') ? fixTimingLine(line, ',') : line))
    .join('\n');
}

function fixVtt(text) {
  return text
    .split(/\r?\n/)
    .map((line) => (line.includes('-->') ? fixTimingLine(line, '.') : line))
    .join('\n');
}

for (const [file, fix] of [
  [process.argv[2], fixSrt],
  [process.argv[3], fixVtt],
]) {
  if (!file) continue;
  writeFileSync(file, fix(readFileSync(file, 'utf8')));
  console.log(`normalised ${file}`);
}
