// State of one test run, shared by global setup, the test worker (which Playwright
// restarts after a failed test) and global teardown. It holds only what cleanup
// needs: ids of the data created, services stopped by a test and the times of the
// login attempts (for the gateway rate limit). Never tokens or passwords.
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { UI_DIR } from './env';

export interface RunState {
  runId: string;
  productIds: number[];
  userIds: number[];
  stoppedServices: string[];
  loginTimes: number[];
}

const RUN_DIR = resolve(UI_DIR, '.run');

export function runId(): string {
  const id = process.env.UI_RUN_ID;
  if (!id) throw new Error('UI_RUN_ID is set by global-setup.ts; run the tests with "npx playwright test"');
  return id;
}

function statePath(id: string): string {
  return resolve(RUN_DIR, `state-${id}.json`);
}

export function createState(id: string): void {
  mkdirSync(RUN_DIR, { recursive: true });
  const state: RunState = { runId: id, productIds: [], userIds: [], stoppedServices: [], loginTimes: [] };
  writeFileSync(statePath(id), JSON.stringify(state, null, 1));
}

export function readState(id: string = runId()): RunState {
  return JSON.parse(readFileSync(statePath(id), 'utf8')) as RunState;
}

export function updateState(change: (state: RunState) => void, id: string = runId()): RunState {
  const state = readState(id);
  change(state);
  writeFileSync(statePath(id), JSON.stringify(state, null, 1));
  return state;
}

export function removeState(id: string): void {
  rmSync(statePath(id), { force: true });
}
