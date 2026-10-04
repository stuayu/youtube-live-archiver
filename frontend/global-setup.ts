import { startE2E, stopE2E } from './scripts/e2e-env.mjs'
export default async function globalSetup() {
  const state = await startE2E()
  if (state.seedEnv) process.env.YLA_E2E_SEED_ENV = JSON.stringify(state.seedEnv)
  return async () => stopE2E(state)
}
