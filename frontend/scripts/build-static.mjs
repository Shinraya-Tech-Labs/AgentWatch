// Static dashboard for the Python package: `npm run build:static`.
//
// Builds the app with output: 'export' (see next.config.js) into a separate dist dir, so the
// standalone server build in .next is left alone, then copies it to panoptis/_dashboard,
// where `panoptis up` serves it and the wheel picks it up.
import { spawnSync } from 'node:child_process';
import { cpSync, existsSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const app = join(here, '..');
const out = join(app, 'out');
const target = join(app, '..', 'panoptis', '_dashboard');

rmSync(out, { recursive: true, force: true });
const next = join(app, 'node_modules', 'next', 'dist', 'bin', 'next');
const r = spawnSync(process.execPath, [next, 'build'], {
  cwd: app,
  stdio: 'inherit',
  env: { ...process.env, PANOPTIS_EXPORT: '1', NEXT_TELEMETRY_DISABLED: '1' },
});
if (r.status !== 0) process.exit(r.status ?? 1);
if (!existsSync(join(out, 'index.html'))) {
  console.error('static build produced no out/index.html');
  process.exit(1);
}
rmSync(target, { recursive: true, force: true });
cpSync(out, target, { recursive: true });
writeFileSync(join(target, '.gitignore'), '*\n');
console.log(`dashboard copied to ${target}`);
