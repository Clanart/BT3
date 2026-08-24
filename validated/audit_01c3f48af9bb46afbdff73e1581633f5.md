Confirmed: `getCheckoutOpts` in `checkout.ts` never sets `interceptHooks`, so `git checkout` (used by `checkoutBranch`) is not one of the hooked-interception paths, unlike `pull`, `push`, `commit`, and `merge`.

### Title
Trampoline token is not scoped to a repository/operation, letting any hook process (or `post-checkout` script) exfiltrate any signed-in account's OAuth token via the credential-helper's `get` command - (File: `app/src/lib/trampoline/trampoline-tokens.ts`)

### Summary
`isValidTrampolineToken` only checks whether a token exists in a single, process-global `Set`, with no association to a repository, working directory, remote host, or git operation. [1](#0-0) 
Combined with the credential-helper's `get` implementation, which resolves the credential purely by the `url=` field supplied in the request stdin (`getCredentialUrl(cred)`) rather than by anything tied to the token/operation that originated the request, this allows any process holding *any* currently-valid trampoline token to request stored GitHub credentials for *any* endpoint known to the shared `AccountsStore`. [2](#0-1) 

### Finding Description
Every git subprocess invocation from Desktop is wrapped with `withTrampolineEnv`, which mints a token via `withTrampolineToken`/`requestTrampolineToken` and injects `DESKTOP_PORT` and `DESKTOP_TRAMPOLINE_TOKEN` into that subprocess's environment. [3](#0-2) 
The `TrampolineServer` accepts any command whose token passes `isValidTrampolineToken`, which is nothing more than set membership — there is no check that the token belongs to the repository/host the caller is trying to authenticate against. [4](#0-3) 

For the credential-helper `get` command, `getGitHubCredential` looks up the account purely by matching the origin of the requested `url=` field against the shared `AccountsStore` (which holds every signed-in account across all open repositories/tabs in the process), with no restriction based on which repository/operation the trampoline token was minted for: [2](#0-1) [5](#0-4) 

Desktop is aware that hook scripts execute inside cloned repositories and can be attacker-influenced (e.g., via a repo-tracked `core.hooksPath`, such as Husky's `.husky` directory installed by a `postinstall` script), which is why a hook-interception sandbox exists (`with-hooks-env.ts` / `hooks-proxy.ts`) that strips all non-`GIT_`/`GITHEAD_` environment variables — explicitly excluding `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` — before spawning the actual hook binary: [6](#0-5) [7](#0-6) 

However, that sandbox is only engaged when the caller explicitly passes `interceptHooks` to `git()`, and `withHooksEnv` returns the *unmodified* environment (still containing `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT`) when `interceptHooks` is not provided: [8](#0-7) 

`checkoutBranch`/`getCheckoutOpts` in `checkout.ts` builds its git execution options without ever setting `interceptHooks`, unlike `pull.ts`, `push.ts`, `commit.ts`, and `merge.ts`: [9](#0-8) [10](#0-9) 

Since `post-checkout` is a recognized/known hook (`getRepoHooks`'s `knownHooks` list includes it) and Husky-style repos routinely wire `post-checkout` (and `pre-commit`, `post-merge`, etc. for the intercepted commands) via a repo-tracked `core.hooksPath`, a malicious repository can ship a `post-checkout` hook that git will execute directly as a child of Desktop's git subprocess, inheriting the *entire* process environment — including the live `DESKTOP_TRAMPOLINE_TOKEN` and `DESKTOP_PORT`, unfiltered by the hooks-proxy sandbox. [11](#0-10) 

With that valid token, the malicious hook can connect to `127.0.0.1:$DESKTOP_PORT` and issue a `CREDENTIALHELPER get` command with a crafted `url=` pointing at `github.com` (or a GitHub Enterprise host), and — because `isValidTrampolineToken` and `getGitHubCredential` perform no scoping to the originating repository/host — the trampoline server will return the OAuth token of whichever account in the shared `AccountsStore` matches that endpoint, even if that account/token was never used for the malicious repository's own remote.

### Impact Explanation
A successful exploitation exfiltrates the user's GitHub.com or GitHub Enterprise OAuth token (as returned by `credWithAccount`), which grants the attacker the full scope of that token (repo read/write, potentially private repos, depending on scopes granted to Desktop). This is a credential-exfiltration issue with high impact, matching the bounty's "credential/token exfiltration" category.

### Likelihood Explanation
Exploitation requires the victim to open/clone an attacker-controlled repository and to check out a branch in it (or otherwise trigger the hook) while signed into a GitHub account in Desktop — a realistic workflow (open source contribution, cloning a project and running its setup instructions). It does not require two windows/repos to be open simultaneously as originally hypothesized in the question; a single malicious repository with an unintercepted hook and a signed-in account elsewhere in the same Desktop instance is sufficient, since the `AccountsStore` used for lookups is global to the whole process, not per-window. This lowers the bar for the attack compared to the "two windows" scenario, but it also means the exact framing in the submitted question (needing a second, currently open trusted repo/window) is not quite accurate — no second window is needed, just a second signed-in account matching the target host, which is the common case.

### Recommendation
- Scope trampoline tokens to the operation/repository that created them (e.g., store the repository path/remote endpoint alongside the token) and validate that the `url=` requested via the credential-helper's `get`/`store`/`erase` commands corresponds to the endpoint the token was issued for before consulting `AccountsStore`.
- Ensure all hook-triggering git commands (notably `checkout`, and any others missing it) pass `interceptHooks` so that `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` are never exposed to hook subprocesses, closing the gap identified in `checkout.ts`.

### Proof of Concept
1. Create a malicious repository that (via a `postinstall`/setup script commonly run after clone, e.g. Husky-style) sets `core.hooksPath` to a repo-tracked directory containing a `post-checkout` script.
2. The `post-checkout` script reads its own environment for `DESKTOP_TRAMPOLINE_TOKEN` and `DESKTOP_PORT` (available because `checkoutBranch` doesn't use `interceptHooks`) and opens a TCP connection to `127.0.0.1:$DESKTOP_PORT`.
3. It sends a `CREDENTIALHELPER` `get` command (per the trampoline wire protocol) with stdin `protocol=https\nhost=github.com\n` and the captured token.
4. `trampoline-server.ts` validates the token via `isValidTrampolineToken` (passes, since it's a live token) and dispatches to `createCredentialHelperTrampolineHandler` → `getCredential` → `getGitHubCredential`, which matches `github.com` against any signed-in account in `AccountsStore` and returns `username=<login>&password=<oauth-token>`.
5. The script exfiltrates the returned token to an attacker-controlled server. [1](#0-0) [9](#0-8) [8](#0-7)

### Citations

**File:** app/src/lib/trampoline/trampoline-tokens.ts (L1-16)
```typescript
const trampolineTokens = new Set<string>()

function requestTrampolineToken() {
  const token = crypto.randomUUID()
  trampolineTokens.add(token)
  return token
}

function revokeTrampolineToken(token: string) {
  trampolineTokens.delete(token)
}

/** Checks if a given trampoline token is valid. */
export function isValidTrampolineToken(token: string) {
  return trampolineTokens.has(token)
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

**File:** app/src/lib/trampoline/trampoline-server.ts (L162-165)
```typescript
  private async processCommand(socket: Socket, command: ITrampolineCommand) {
    if (!isValidTrampolineToken(command.trampolineToken)) {
      throw new Error('Tried to use invalid trampoline token')
    }
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

**File:** app/src/lib/hooks/hooks-proxy.ts (L245-255)
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

**File:** app/src/lib/git/checkout.ts (L38-51)
```typescript
async function getCheckoutOpts(
  repository: Repository,
  title: string,
  target: string,
  currentRemote: IRemote | null,
  progressCallback?: ProgressCallback,
  initialDescription?: string
): Promise<IGitStringExecutionOptions> {
  const opts: IGitStringExecutionOptions = {
    env: await envForRemoteOperation(
      getFallbackUrlForProxyResolve(repository, currentRemote)
    ),
    expectedErrors: AuthenticationErrors,
  }
```

**File:** app/src/lib/git/pull.ts (L43-57)
```typescript
  let opts: IGitStringExecutionOptions = {
    env: await envForRemoteOperation(remote.url),
    // git pull triggers merge or rebase hooks depending on config, instead of
    // trying to check pull.rebase and friends we'll just intercept all possible
    // hooks that could be run as part of a pull operation.
    interceptHooks: [
      'pre-merge-commit',
      'prepare-commit-msg',
      'commit-msg',
      'post-merge',
      'pre-rebase',
      'pre-commit',
      'post-rewrite',
    ],
  }
```

**File:** app/src/lib/hooks/get-repo-hooks.ts (L10-39)
```typescript
const knownHooks = [
  'applypatch-msg',
  'pre-applypatch',
  'post-applypatch',
  'pre-commit',
  'pre-merge-commit',
  'prepare-commit-msg',
  'commit-msg',
  'post-commit',
  'pre-rebase',
  'post-checkout',
  'post-merge',
  'pre-push',
  'pre-receive',
  'update',
  'proc-receive',
  'post-receive',
  'post-update',
  'reference-transaction',
  'push-to-checkout',
  'pre-auto-gc',
  'post-rewrite',
  'sendemail-validate',
  'fsmonitor-watchman',
  'p4-changelist',
  'p4-prepare-changelist',
  'p4-post-changelist',
  'p4-pre-submit',
  'post-index-change',
]
```
