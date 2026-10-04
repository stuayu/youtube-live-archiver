import { spawn, spawnSync } from 'node:child_process'
import { createServer } from 'node:net'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { setTimeout as sleep } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'

const frontendDir = resolve(fileURLToPath(new URL('..', import.meta.url)))
const repoDir = resolve(frontendDir, '..')
const backendDir = join(repoDir, 'backend')
const py =
  process.env.YLA_PYTHON ||
  join(
    backendDir,
    '.venv',
    process.platform === 'win32' ? 'Scripts' : 'bin',
    process.platform === 'win32' ? 'python.exe' : 'python',
  )
const state = {
  external: false,
  children: [],
  pgServer: undefined,
  tmpDir: '',
  adminUrl: '',
  database: '',
  apiPort: 0,
  uiPort: 0,
}
function child(command, args, options = {}) {
  const p = spawn(command, args, { stdio: ['ignore', 'pipe', 'pipe'], ...options })
  state.children.push(p)
  p.stdout?.resume()
  p.stderr?.on('data', (chunk) => process.stderr.write(chunk))
  return p
}
async function freePort() {
  const server = createServer()
  await new Promise((resolvePromise, reject) =>
    server.once('error', reject).listen(0, '127.0.0.1', resolvePromise),
  )
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('free port unavailable')
  await new Promise((resolvePromise) => server.close(resolvePromise))
  return address.port
}
function waitLine(p) {
  return new Promise((resolvePromise, reject) => {
    let text = ''
    const timeout = setTimeout(() => reject(new Error('pgserver startup timeout')), 30000)
    p.stdout.on('data', (chunk) => {
      text += chunk.toString()
      const index = text.indexOf('\n')
      if (index >= 0) {
        clearTimeout(timeout)
        resolvePromise(text.slice(0, index).trim())
      }
    })
    p.once('exit', (code) => reject(new Error(`pgserver exited ${code}: ${text}`)))
  })
}
function dbUrl(base, name) {
  const normalized = base.replace('postgresql+psycopg://', 'postgresql://')
  const queryAt = normalized.indexOf('?')
  const authorityAndPath = queryAt < 0 ? normalized : normalized.slice(0, queryAt)
  const query = queryAt < 0 ? '' : normalized.slice(queryAt)
  const authAt = authorityAndPath.indexOf('@')
  const pathAt = authorityAndPath.indexOf('/', authAt >= 0 ? authAt : authorityAndPath.indexOf('://') + 3)
  const pathStart = pathAt < 0 ? authorityAndPath.length : pathAt
  return `${authorityAndPath.slice(0, pathStart)}/${name}${query}`.replace(
    'postgresql://',
    'postgresql+psycopg://',
  )
}
function psycopgUrl(value) {
  return value.startsWith('postgresql://') ? value.replace('postgresql://', 'postgresql+psycopg://') : value
}
function run(command, args, options = {}) {
  const result = spawnSync(command, args, { stdio: 'inherit', ...options })
  if (result.status !== 0) throw new Error(`${command} ${args.join(' ')} failed (${result.status})`)
}
async function waitHttp(url, childProcess) {
  for (let i = 0; i < 100; i++) {
    if (childProcess.exitCode !== null) throw new Error(`server exited (${childProcess.exitCode})`)
    try {
      const response = await fetch(url)
      if (response.ok) return
    } catch {
      /* wait until bound */
    }
    await sleep(250)
  }
  throw new Error(`server did not become ready: ${url}`)
}
export async function startE2E() {
  if (process.env.YLA_E2E_URL) {
    state.external = true
    return { ...state, baseURL: process.env.YLA_E2E_URL }
  }
  try {
    state.tmpDir = await mkdtemp(join(tmpdir(), 'yla-e2e-'))
    let baseUrl = process.env.YLA_TEST_DATABASE_URL
    if (!baseUrl) {
      const pgdata = join(state.tmpDir, 'pgdata')
      const pg = child(py, [join(backendDir, 'tests', 'e2e_pgserver.py'), pgdata], { cwd: backendDir })
      state.pgServer = pg
      const uri = await waitLine(pg)
      baseUrl = psycopgUrl(uri)
    }
    baseUrl = psycopgUrl(baseUrl)
    state.adminUrl = baseUrl
    run(
      py,
      [join(backendDir, 'tests', 'e2e_database.py'), 'cleanup', baseUrl, String(Date.now() - 3600000)],
      {
        cwd: backendDir,
      },
    )
    state.database = `yla_e2e_${process.pid}_${Date.now().toString(36)}`
    run(py, [join(backendDir, 'tests', 'e2e_database.py'), 'create', baseUrl, state.database], {
      cwd: backendDir,
    })
    const databaseUrl = dbUrl(baseUrl, state.database)
    const storageRoot = join(state.tmpDir, 'media')
    const dataDir = join(state.tmpDir, 'data')
    await import('node:fs/promises').then((fs) => fs.mkdir(storageRoot, { recursive: true }))
    const fakeState = join(state.tmpDir, 'fake-ytdlp.json')
    const fakeChannels = Object.fromEntries(
      ['desktop', 'webkit', 'tablet', 'mobile'].flatMap((name) => {
        const config = {
          channel: `E2E チャンネル ${name}`,
          channel_id: `UC${name.padEnd(22, 'a')}`,
          entries: [],
        }
        return [
          [`@e2e-${name}`, config],
          [config.channel_id, config],
        ]
      }),
    )
    await import('node:fs/promises').then((fs) =>
      fs.writeFile(fakeState, JSON.stringify({ channels: fakeChannels, videos: {} })),
    )
    const env = {
      ...process.env,
      DATABASE_URL: databaseUrl,
      STORAGE_ROOT: storageRoot,
      DATA_DIR: dataDir,
      YT_DLP_PATH: join(backendDir, 'tests', 'fakes', 'fake_ytdlp.py'),
      FAKE_YTDLP_STATE: fakeState,
      SCHEDULER_ENABLED: 'false',
      APP_RELOAD: 'false',
      PYTHONPATH: backendDir,
    }
    state.seedEnv = {
      DATABASE_URL: databaseUrl,
      STORAGE_ROOT: storageRoot,
      DATA_DIR: dataDir,
      YT_DLP_PATH: env.YT_DLP_PATH,
      FAKE_YTDLP_STATE: fakeState,
      PYTHONPATH: backendDir,
      YLA_E2E_ADMIN_URL: baseUrl,
      YLA_E2E_DATABASE_NAME: state.database,
    }
    run(py, ['-m', 'alembic', '-c', 'alembic.ini', 'upgrade', 'head'], { cwd: backendDir, env })
    run(py, [join(backendDir, 'tests', 'e2e_seed.py')], { cwd: backendDir, env })
    state.apiPort = await freePort()
    const api = child(
      py,
      ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(state.apiPort)],
      { cwd: backendDir, env },
    )
    await waitHttp(`http://127.0.0.1:${state.apiPort}/api/status`, api)
    state.uiPort = 4173
    const vite = child(
      process.execPath,
      [
        join(frontendDir, 'node_modules', 'vite', 'bin', 'vite.js'),
        '--force',
        '--host',
        '127.0.0.1',
        '--port',
        String(state.uiPort),
        '--strictPort',
      ],
      { cwd: frontendDir, env: { ...process.env, VITE_API_URL: `http://127.0.0.1:${state.apiPort}/api` } },
    )
    await waitHttp(`http://127.0.0.1:${state.uiPort}/`, vite)
    return { ...state, baseURL: `http://127.0.0.1:${state.uiPort}` }
  } catch (error) {
    await stopE2E(state)
    throw error
  }
}
function stopProcess(processHandle) {
  if (processHandle.exitCode !== null) return
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid', String(processHandle.pid), '/t', '/f'], { stdio: 'ignore' })
  } else {
    processHandle.kill('SIGTERM')
  }
}
export async function stopE2E(context = state) {
  if (context.external) return
  const owned = context.children || []
  const applicationProcesses = owned.filter((processHandle) => processHandle !== context.pgServer)
  for (const processHandle of [...applicationProcesses].reverse()) stopProcess(processHandle)
  await Promise.all(
    applicationProcesses.map((processHandle) =>
      processHandle.exitCode !== null
        ? Promise.resolve()
        : new Promise((resolvePromise) => {
            const timeout = setTimeout(resolvePromise, 3000)
            processHandle.once('exit', () => {
              clearTimeout(timeout)
              resolvePromise()
            })
          }),
    ),
  )
  if (context.database && context.adminUrl) {
    try {
      run(py, [join(backendDir, 'tests', 'e2e_database.py'), 'drop', context.adminUrl, context.database], {
        cwd: backendDir,
      })
    } catch (error) {
      process.stderr.write(`E2E database cleanup failed: ${String(error)}\n`)
    }
  }
  if (context.pgServer) stopProcess(context.pgServer)
  if (context.pgServer)
    await new Promise((resolvePromise) =>
      context.pgServer.exitCode !== null ? resolvePromise() : context.pgServer.once('exit', resolvePromise),
    )
  if (context.tmpDir) await rm(context.tmpDir, { recursive: true, force: true })
}
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.once(signal, () => {
    void stopE2E().finally(() => process.exit(signal === 'SIGINT' ? 130 : 143))
  })
}
