Confirmed: `clone()` in `app/src/lib/git/clone.ts` invokes `git clone --recursive`, which recursively initializes/fetches submodules using URLs defined in the untrusted repository's own `.gitmodules` file, all under a single `withTrampolineEnv`/trampoline-token session (`app/src/lib/git/core.ts:277`, `app/src/lib/trampoline/trampoline-environment.ts:93-147`).

### Title
Attacker-Controlled `.gitmodules`/LFS URLs Can Redirect Desktop's Credential Helper to Exfiltrate the Signed-In User's GitHub Token to a Different Repository - (File: app/src/lib/trampoline/trampoline-credential-helper.ts)

### Summary
GitHub Desktop authenticates every git HTTPS request made during a single clone/fetch/pull operation using one shared "trampoline" session, and decides which stored account's OAuth token to hand back based solely on the **origin of the URL supplied by the git subprocess in the credential-helper protocol request** (`host`/`url` fields), never on the remote or repository the user actually asked Desktop to operate on. Because `git clone --recursive` will fetch submodules (and LFS smudge filters can fetch objects) from URLs taken from the untrusted repository's own `.gitmodules`/`.lfsconfig`, a malicious public repository can point a submodule at an arbitrary GitHub-hosted URL (including a private repository the victim happens to have access to) and cause Desktop to silently hand the victim's real GitHub credentials to that request within the same trampoline session used for the malicious clone.

### Finding Description
The "commitment" analog here is `isValidTrampolineToken` (`app/src/lib/trampoline/trampoline-tokens.ts:14-16`), which is the only authentication check performed before a credential-helper command is processed (`app/src/lib/trampoline/trampoline-server.ts:162-165`). Like the PLONK bug's `γ`/`β` challenges, this "challenge" (token validity) is derived from far too little context: it merely proves *some* Desktop-spawned git subprocess is talking back to Desktop over the loopback socket — it is never bound to the specific repository/remote the operation was started for.

Downstream, `getCredential` (`app/src/lib/trampoline/trampoline-credential-helper.ts:94-135`) and `getGitHubCredential`/`findGitHubTrampolineAccount` (`app/src/lib/trampoline/find-account.ts:20-29`) select which stored `Account`'s OAuth token to release purely by matching `new URL(getHTMLURL(a.endpoint)).origin === parsedUrl.origin`, where `parsedUrl` comes from `getCredentialUrl(cred)` (`app/src/lib/trampoline/trampoline-environment.ts:46-59`) — a value built entirely from the `url`/`protocol`/`host` fields that the *git subprocess* sends over the socket. Those fields are populated by git from whatever remote URL it is currently contacting, which for `git clone --recursive` (`app/src/lib/git/clone.ts:88-125`) includes submodule URLs read from the cloned repository's own `.gitmodules` file, and for LFS-enabled repos includes URLs from `.lfsconfig`.

`withTrampolineEnv` (`app/src/lib/trampoline/trampoline-environment.ts:93-147`) sets `GIT_CONFIG_PARAMETERS` with `credential.helper=desktop` globally for the whole operation and explicitly notes it is done this way "because we want commands invoked by filters (i.e. Git LFS) to be able to pick up our configuration" — i.e., the credential helper is intentionally scoped to the entire multi-URL operation, not to the single top-level remote the user is cloning/fetching.

The existing guard, `isClonePathSensitive` in `clone.ts:16-47`, only defends against path traversal for the clone *destination*; it does nothing to constrain which remote hosts credentials may be released to during the operation. No code path checks that the credential request's origin matches the top-level `url` argument passed into `clone()`/`fetch()`/`pull()`.

### Impact Explanation
An attacker who publishes a public GitHub repository can add a `.gitmodules` entry pointing to `https://github.com/<victim-org>/<private-repo>.git` (a repository the victim already has access to, e.g. via their employer or another project). When the victim clones the attacker's repository in Desktop, `git clone --recursive` will attempt to fetch the submodule and invoke the shared credential helper for that URL. Because the matching logic only compares origins (`github.com`), Desktop will transparently hand over the victim's real GitHub OAuth token, allowing the submodule fetch to succeed and pull down private repository contents that then land in the victim's working directory / local git objects, where the same malicious repo can contain build scripts, hooks, or other automation to read and exfiltrate that data. This is a confused-deputy credential/token misuse (unauthorized cross-repository access using the user's own long-lived OAuth token) triggered purely by cloning an attacker-controlled repository — squarely in the "attacker controls a cloned/fetched repository" + "credential/token exfiltration" category.

### Likelihood Explanation
Likelihood is moderate-to-high: no special user interaction beyond the normal "Clone repository" action is required, `.gitmodules`/`.lfsconfig` manipulation is a well-known, easily reproducible technique, and `--recursive` submodule fetching plus LFS filter invocation are default/standard Desktop clone behaviors. The victim only needs to already have access to the targeted private resource for the attack to yield useful data, which is a realistic precondition for supply-chain/social-engineering-adjacent scenarios (e.g., targeting employees of a known organization) — but per the task's exclusion rules, no social engineering beyond "user clones a public repo" is assumed here.

### Recommendation
Bind the trampoline/credential-helper "challenge" to the actual operation context instead of trusting attacker-influenced request fields alone: record the top-level remote URL(s)/allowed host set for each `withTrampolineEnv` session (keyed by trampoline token, similar to `trampolineEnvironmentPath`), and in `getCredential`/`findGitHubTrampolineAccount` only release a stored GitHub account's token when the requested credential URL's origin matches an explicitly authorized origin for that session (the top-level clone/fetch/push remote, not just "any known GitHub origin"). For submodule/LFS URLs pointing to unrelated hosts, fall back to prompting the user (as is already done for unknown/generic endpoints) rather than silently reusing the primary account's token.

### Proof of Concept
1. Attacker creates public repo `evil/repo` with a `.gitmodules` file:
   ```
   [submodule "x"]
     path = x
     url = https://github.com/victim-org/private-repo.git
   ```
2. Victim, signed into Desktop with an account that has read access to `victim-org/private-repo`, clones `evil/repo` via Desktop's "Clone repository" UI.
3. Desktop runs `git clone --recursive` (`app/src/lib/git/clone.ts:88-125`) under one `withTrampolineEnv` token.
4. Git's submodule update step contacts `https://github.com/victim-org/private-repo.git` and calls the configured `credential.helper=desktop`, sending `host=github.com` in the credential-helper protocol.
5. `getCredential` → `getGitHubCredential` → `findGitHubTrampolineAccount` (`find-account.ts:20-29`) matches solely on `origin === 'https://github.com'` and returns the victim's real OAuth token, without any check that the operation was authorized for `victim-org/private-repo`.
6. The submodule is fetched using the victim's credentials and lands in the local clone of the attacker's repository, alongside attacker-controlled tooling (e.g. `.git/hooks`, npm scripts) capable of reading and exfiltrating it. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** app/src/lib/trampoline/trampoline-tokens.ts (L13-16)
```typescript
/** Checks if a given trampoline token is valid. */
export function isValidTrampolineToken(token: string) {
  return trampolineTokens.has(token)
}
```

**File:** app/src/lib/trampoline/trampoline-server.ts (L162-172)
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

**File:** app/src/lib/git/clone.ts (L68-125)
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
```

**File:** app/src/lib/git/core.ts (L277-295)
```typescript
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
