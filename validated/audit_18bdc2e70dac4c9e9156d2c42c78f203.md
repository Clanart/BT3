## Title
Malicious repository hook can steal the user's real GitHub credentials via the trampoline credential-helper socket during any hook-intercepted Git operation - (File: `app/src/lib/trampoline/trampoline-environment.ts`, `app/src/lib/trampoline/trampoline-server.ts`, `app/src/lib/hooks/get-repo-hooks.ts`)

### Summary
`withTrampolineEnv` sets up a loopback TCP server (`trampolineServer`) and a short-lived `DESKTOP_TRAMPOLINE_TOKEN` that is placed directly in the environment of the spawned Git process [1](#0-0) . That token is the *only* credential the server uses to authorize a `CREDENTIALHELPER get` request that returns the user's real GitHub OAuth token [2](#0-1) [3](#0-2) . Git hooks discovered in the repository (`getRepoHooks`) are executed as native child processes of the same Git invocation and therefore inherit this environment, including `DESKTOP_TRAMPOLINE_TOKEN` and `DESKTOP_PORT` [4](#0-3) . Because a Git hook script is ordinary attacker-controlled content that ships inside a cloned/fetched or manually-added `.git` directory (or is redirected to via `core.hooksPath`), any such hook can read those two environment variables and connect directly to the trampoline server itself, bypassing the real `git credential fill` invocation entirely, and simply ask the credential-helper handler for the credential associated with the operation's endpoint.

### Finding Description
The invariant that is supposed to hold is: *only Git's own credential-helper/askpass subprocess, acting on Git's behalf for the specific remote URL being authenticated, should be able to retrieve the account token for the current operation.* The trampoline design breaks this invariant because authorization is reduced to possession of a random token string placed in the process environment of the *entire* Git invocation, not scoped to the actual askpass/credential-helper child process [1](#0-0) .

Git hooks (`pre-commit`, `post-checkout`, `pre-push`, etc.) run as children of the same `git` process that Desktop spawned via `spawnGit`/`withTrampolineEnv`, so they inherit `env` unless explicitly stripped [5](#0-4) . `getRepoHooks` enumerates hooks purely from `core.hooksPath`/`.git/hooks` on disk with no notion of trust, and Desktop's hook interception mechanism (`withHooksEnv`) explicitly reads `opts.interceptHooks` for operations such as `commit`, `pull`, `push`, and `merge`, meaning hooks are expected to run during these common, frequent operations [6](#0-5) .

`processCommand` on the trampoline server validates only that the token is a currently-issued, unexpired token (`isValidTrampolineToken`) - it performs no check that the calling process is the specific `desktop-askpass-trampoline` binary spawned for that operation, nor that the request corresponds to the credential URL Git is currently authenticating against [2](#0-1) . The `createCredentialHelperTrampolineHandler`'s `get` path will happily return the stored GitHub account token for whatever `url`/`host`/`protocol` fields the caller supplies in its request body, since `getCredentialUrl` simply trusts the caller-provided map [7](#0-6) [8](#0-7) .

### Impact Explanation
A hook shipped in a maliciously prepared repository (e.g. a folder the user is asked to "Add Local Repository", or a repository whose `.git` metadata is otherwise attacker-influenced) can silently exfiltrate the user's real GitHub OAuth token the next time the user performs any Git operation that Desktop intercepts hooks for (commit, push, pull, merge). This is a direct token/credential exfiltration primitive - full account compromise for whatever scopes the stored Desktop token carries - without any additional user interaction beyond a normal Git action inside the app.

### Likelihood Explanation
Any Git operation performed through Desktop on a repository containing a malicious hook (or an overridden `core.hooksPath`) triggers this window automatically; the attacker does not need the user to click anything unusual, and the hook interception feature is wired into common day-to-day flows (`commit.ts`, `pull.ts`, `push.ts`, `merge.ts`) [9](#0-8) [10](#0-9) . The token is short-lived (revoked in the `finally` of `withTrampolineToken`) but remains valid for the entire duration of the surrounding Git command, which is exactly when hooks fire [11](#0-10) .

### Recommendation
Do not pass `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` to hook subprocesses: scrub them from the environment used to spawn hooks (the `process-proxy`/hooks-proxy layer already creates a separate proxy environment for hooks - it should not inherit the outer trampoline env), or bind the credential-helper socket authorization to the specific askpass/credential-helper trampoline binary invocation rather than a value visible to any child of the Git process. Additionally, validate that the `url`/`host` supplied in a `get` request matches the remote Git is actually contacting for that token, rather than trusting caller-supplied credential fields verbatim.

### Proof of Concept
1. Prepare a repository (e.g. distributed as a zip or added via "Add Local Repository") containing an executable `.git/hooks/post-checkout` script.
2. The script reads `DESKTOP_TRAMPOLINE_TOKEN` and `DESKTOP_PORT` from its environment (inherited from the parent `git` process spawned by `spawnGit`).
3. The script opens a TCP connection to `127.0.0.1:${DESKTOP_PORT}` and sends a `CREDENTIALHELPER get` command (per the trampoline wire protocol) with `stdin` containing `protocol=https\nhost=github.com\n`, using the stolen `trampolineToken`.
4. `trampoline-server.ts`'s `processCommand` validates the token via `isValidTrampolineToken` and dispatches to `createCredentialHelperTrampolineHandler`, which calls `getGitHubCredential` and returns the user's real GitHub username/token pair, formatted per `formatCredential` [12](#0-11) .
5. The user simply checks out a branch (any operation that runs `post-checkout` through Desktop's normal UI); the hook exfiltrates the credential to an attacker-controlled endpoint in the background.

### Citations

**File:** app/src/lib/trampoline/trampoline-environment.ts (L46-59)
```typescript
export const getCredentialUrl = (cred: Map<string, string>) => {
  const u = cred.get('url')
  if (u) {
    return new URL(u)
  }

  const protocol = cred.get('protocol') ?? ''
  const username = cred.get('username')
  const user = username ? `${encodeURIComponent(username)}@` : ''
  const host = cred.get('host') ?? ''
  const path = cred.get('path') ?? ''

  return new URL(`${protocol}://${user}${host}/${path}`)
}
```

**File:** app/src/lib/trampoline/trampoline-environment.ts (L123-147)
```typescript
      return await fn({
        DESKTOP_PORT: await trampolineServer.getPort(),
        DESKTOP_TRAMPOLINE_TOKEN: token,
        GIT_ASKPASS: '',
        // This warrants some explanation. We're configuring the
        // credential helper using environment variables rather than
        // arguments (i.e. -c credential.helper=) because we want commands
        // invoked by filters (i.e. Git LFS) to be able to pick up our
        // configuration. Arguments passed to git commands are not passed
        // down to filters.
        //
        // We're using the undocumented GIT_CONFIG_PARAMETERS environment
        // variable over the documented GIT_CONFIG_{COUNT,KEY,VALUE} due
        // to an apparent bug either in a Windows Python runtime
        // dependency or in a Python project commonly used to manage hooks
        // which isn't able to handle the blank environment variables we
        // need when using GIT_CONFIG_*.
        //
        // See https://github.com/desktop/desktop/issues/18945
        // See https://github.com/git/git/blob/ed155187b429a/config.c#L664
        GIT_CONFIG_PARAMETERS: `${gitEnvConfigPrefix}'credential.helper=' 'credential.helper=desktop'`,

        GIT_USER_AGENT: await GitUserAgent(),
        ...sshEnv,
      })
```

**File:** app/src/lib/trampoline/trampoline-server.ts (L162-176)
```typescript
  private async processCommand(socket: Socket, command: ITrampolineCommand) {
    if (!isValidTrampolineToken(command.trampolineToken)) {
      throw new Error('Tried to use invalid trampoline token')
    }

    const handler = this.commandHandlers.get(command.identifier)

    if (handler === undefined) {
      socket.end()
      return
    }

    const result = await handler(command).catch(e =>
      log.error('Error processing trampoline command', e)
    )
```

**File:** app/src/lib/trampoline/trampoline-tokens.ts (L13-16)
```typescript
/** Checks if a given trampoline token is valid. */
export function isValidTrampolineToken(token: string) {
  return trampolineTokens.has(token)
}
```

**File:** app/src/lib/trampoline/trampoline-tokens.ts (L24-37)
```typescript
export async function withTrampolineToken<T>(
  fn: (token: string) => Promise<T>
): Promise<T> {
  const token = requestTrampolineToken()
  let result

  try {
    result = await fn(token)
  } finally {
    revokeTrampolineToken(token)
  }

  return result
}
```

**File:** app/src/lib/hooks/get-repo-hooks.ts (L41-52)
```typescript
// getRepoHooks is used by withHooksEnv which is used by git in core.ts so we
// have to be careful to not accidentally run into a circular dependency here
// where we invoke git which calls us which calls git which calls us, etc. To
// avoid that we call dugite directly here.
const git = (args: string[], path: string) =>
  exec(args, path).then(({ exitCode, stdout, stderr }) => {
    return exitCode === 0
      ? stdout
      : Promise.reject(
          new Error(`Git command failed with exit code ${exitCode}: ${stderr}`)
        )
  })
```

**File:** app/src/lib/hooks/get-repo-hooks.ts (L76-107)
```typescript
export async function* getRepoHooks(path: string, filter?: string[]) {
  const hooksPath = await getConfigValue(path, 'core.hooksPath')
    .catch(() => getHooksPath(path))
    .then(p => resolve(path, p))

  const files = await readdir(hooksPath, { withFileTypes: true })
    .then(entries => entries.filter(x => x.isFile()))
    .catch(() => [])

  const matchAll = filter?.includes('*')

  for (const file of files) {
    const hookName = basename(file.name, '.exe')

    if (matchAll || filter?.includes(hookName) === false) {
      continue
    }

    if (!knownHooks.includes(hookName)) {
      continue
    }

    if (__WIN32__) {
      // On Windows we have to assume that any valid hook name is executable
      // because the executable bit is not used there. Git looks for a shebang
      // but that seems expensive to check here :shrug:
      yield hookName
    } else if (await isExecutable(join(file.parentPath, file.name))) {
      yield hookName
    }
  }
}
```

**File:** app/src/lib/git/spawn.ts (L21-38)
```typescript
export const spawnGit = (
  args: string[],
  path: string,
  name: string,
  options?: SpawnOptions
) =>
  withTrampolineEnv(
    trampolineEnv =>
      GitPerf.measure(`${name}: git ${args.join(' ')}`, async () =>
        spawn(args, path, {
          ...options,
          env: { ...options?.env, ...trampolineEnv },
        })
      ),
    path,
    options?.isBackgroundTask ?? false,
    options?.env
  )
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-104)
```typescript
/** Implementation of the 'get' git credential helper command */
async function getCredential(cred: Credential, store: Store, token: string) {
  const ghCred = await getGitHubCredential(cred, store)

  if (ghCred) {
    return ghCred
  }

  const endpointKind = await getEndpointKind(cred, store)
  const accounts = await store.getAll()

  const endpoint = `${getCredentialUrl(cred)}`
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L220-248)
```typescript
export const createCredentialHelperTrampolineHandler: (
  store: AccountsStore
) => TrampolineCommandHandler = (store: Store) => async command => {
  const firstParameter = command.parameters.at(0)
  if (!firstParameter) {
    return undefined
  }

  const { trampolineToken: token } = command
  const input = parseCredential(command.stdin)

  if (__DEV__) {
    debug(
      `${firstParameter}\n${command.stdin
        .replaceAll(/^password=.*$/gm, 'password=***')
        .replaceAll(/^(.*)$/gm, '  $1')
        .trimEnd()}`
    )
  }

  try {
    if (firstParameter === 'get') {
      const cred = await getCredential(input, store, token)
      if (!cred) {
        const endpoint = `${getCredentialUrl(input)}`
        info(`could not find credential for ${endpoint}`)
        setHasRejectedCredentialsForEndpoint(token, endpoint)
      }
      return cred ? formatCredential(cred) : undefined
```

**File:** app/src/lib/git/commit.ts (L1-1)
```typescript
import { git, HookCallbackOptions, parseCommitSHA } from './core'
```

**File:** app/src/lib/git/push.ts (L1-1)
```typescript
import { git, HookCallbackOptions, IGitStringExecutionOptions } from './core'
```
