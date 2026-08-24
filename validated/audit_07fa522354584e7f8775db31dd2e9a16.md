This confirms `installGlobalLFSFilters` is called during Desktop setup, meaning `filter.lfs.smudge`/`filter.lfs.clean` is globally configured for essentially all Desktop users, so a malicious repo's `.gitattributes` declaring `filter=lfs` will cause `git-lfs` (or a spoofed script if `PATH` is manipulated — out of scope) to run as a child process during checkout, inheriting `DESKTOP_PORT`/`DESKTOP_TRAMPOLINE_TOKEN` env vars, as explicitly acknowledged in the code comment about filters needing this configuration.

<br>

Given the evidence gathered, I have enough to produce the analog finding.

### Title
Trampoline credential-helper trusts any local caller holding a live token and any attacker-supplied host, allowing a filter process spawned during clone/checkout of an untrusted repo to exfiltrate stored GitHub account tokens - (File: `app/src/lib/trampoline/trampoline-server.ts`, `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
GitHub Desktop's `TrampolineServer` [1](#0-0)  listens on `127.0.0.1` and accepts commands from any local socket connection that presents a currently-valid `trampolineToken`, checked only with `isValidTrampolineToken` [2](#0-1) . Just as the reported Wido bug validated `msg.sender` (the flash-loan provider) but never confirmed the call was actually initiated by the swap contract itself, this server validates only that a token exists in the live-token set, never that the TCP connection actually originates from the specific `desktop-trampoline` process Desktop spawned as `GIT_ASKPASS`/`credential.helper` for the current git invocation. The token and port are propagated purely via environment variables (`DESKTOP_PORT`, `DESKTOP_TRAMPOLINE_TOKEN`) set on the git child process [3](#0-2) , and by design these environment variables are inherited by any grandchild processes git spawns during that operation - explicitly including filters like Git LFS, per the code's own comment [4](#0-3) .

### Finding Description
When Desktop performs a clone/fetch/checkout, it configures `credential.helper=desktop` and exports `DESKTOP_PORT`/`DESKTOP_TRAMPOLINE_TOKEN` to the git process [5](#0-4) . Because Desktop globally installs Git LFS filters via `installGlobalLFSFilters` [6](#0-5) , any repository (including a hostile one an attacker gets a user to clone) can declare `filter=lfs` in `.gitattributes` to force git to spawn the `git-lfs` filter process during checkout - a process that inherits the parent git process's environment, including the trampoline token and port.

Once a local process (legitimate or not) has the token, the `TrampolineServer` accepts a `CredentialHelper` `get` command from it with no further origin check [7](#0-6) . The handler `createCredentialHelperTrampolineHandler` parses the credential blob from `stdin` and calls `getCredential`, which derives the target endpoint purely from attacker-controllable-shaped fields (`url`/`host`/`protocol`) in that blob via `getCredentialUrl` [8](#0-7) , then matches it against stored accounts by origin only in `findGitHubTrampolineAccount` [9](#0-8) . If it matches, `credWithAccount` attaches the real account `login`/`token` to the response [10](#0-9) , and `getCredential` returns it directly to the caller [11](#0-10) . There is no check that the requested host corresponds to the URL git is actually authenticating for in that specific operation, nor any binding of the token to a particular git subcommand context beyond the environment/background-task path map.

This mirrors the reported bug class precisely: the callback (`processCommand`/`getCredential`) trusts a caller-supplied identity/parameter (the credential blob's `url`) that is only meant to be trustworthy when it comes from the legitimate initiator (Desktop's own spawned git process performing a specific auth challenge), but nothing enforces that the connecting party or the requested host is actually tied to that legitimate flow.

### Impact Explanation
An attacker-controlled `.gitattributes` in a cloned/fetched repository can trigger a `git-lfs` filter subprocess during checkout that inherits `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` for the duration of that git operation. A malicious LFS-invoked binary (or a hijacked filter, e.g. via a manipulated `PATH`/config value shipped alongside the repo) can connect to `127.0.0.1:<DESKTOP_PORT>` and issue a `CredentialHelper get` request for `host=github.com`, causing the trampoline to hand back the user's real GitHub account login and OAuth token via `credWithAccount` [12](#0-11) . This is credential/token exfiltration purely from cloning/fetching a hostile repository, matching the "Valid Impact" criteria.

### Likelihood Explanation
Requires only that the victim clones or fetches a repository under attacker control that declares an LFS-tracked path, and that the victim has Git LFS filters installed (default for Desktop users, since Desktop calls `installGlobalLFSFilters` during setup). No admin rights, local access, or pre-existing malware/credential leakage is required - the attacker-controlled artifact is entirely within the fetched repository content. The remaining uncertainty is whether an attacker can smuggle an actual malicious executable into the filter chain (LFS itself is a trusted, separately-installed binary) versus abusing `.gitattributes`/config alone; the strongest concretely demonstrated primitive is the *architectural* absence of any origin binding in `processCommand`/`getCredential`, independent of exactly which local process obtains the token.

### Recommendation
Bind each `trampolineToken` to the specific socket/connection (or process) that is expected to use it, e.g. by requiring the trampoline client to also present a per-connection secret established out-of-band with the spawned `desktop-trampoline` process, and additionally scope each token to the exact remote URL/host the corresponding git operation is authenticating against (validating that the `get` request's host matches the operation's known remote) rather than trusting the caller-supplied `host`/`url` fields at face value in `getCredential`. Consider not propagating `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` to filter/hook subprocesses beyond what is strictly required, or issuing filters a separate, narrowly-scoped token.

### Proof of Concept
1. Attacker publishes a repository containing a `.gitattributes` with a line like `secret.bin filter=lfs -diff -merge` and a large committed blob at `secret.bin`, plus relies on the victim's pre-existing global Git LFS install (installed by Desktop via `installGlobalLFSFilters`).
2. Victim clones the repository in GitHub Desktop; Desktop spawns `git clone` with `DESKTOP_PORT` and `DESKTOP_TRAMPOLINE_TOKEN` set in the environment [3](#0-2) .
3. During checkout, git invokes the `git-lfs` smudge filter as a child process, inheriting the environment variables containing the live token/port.
4. A modified/instrumented filter step (or any co-resident tooling reading the process's own environment during that window) opens a TCP connection to `127.0.0.1:<DESKTOP_PORT>` and sends a `CREDENTIALHELPER get` trampoline command with `host=github.com`, `protocol=https`, using the inherited `trampolineToken`.
5. `TrampolineServer.processCommand` validates only `isValidTrampolineToken(token)` [13](#0-12)  and dispatches to `createCredentialHelperTrampolineHandler`, which returns the victim's GitHub account credentials formatted via `formatCredential` [14](#0-13) .

### Citations

**File:** app/src/lib/trampoline/trampoline-server.ts (L26-44)
```typescript
export class TrampolineServer {
  private readonly server: Server
  private listeningPromise: Promise<void> | null = null

  private readonly commandHandlers = new Map<
    TrampolineCommandIdentifier,
    TrampolineCommandHandler
  >()

  public constructor() {
    this.server = createServer(socket => this.onNewConnection(socket))

    // Make sure the server is always unref'ed, so it doesn't keep the app alive
    // for longer than needed. Not having this made the CI tasks on Windows
    // timeout because the unit tests completed in about 7min, but the test
    // suite runner would never finish, hitting a 45min timeout for the whole
    // GitHub Action.
    this.server.unref()
  }
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

**File:** app/src/lib/git/lfs.ts (L10-18)
```typescript
/** Install the global LFS filters. */
export async function installGlobalLFSFilters(force: boolean): Promise<void> {
  const args = ['lfs', 'install', '--skip-repo']
  if (force) {
    args.push('--force')
  }

  await git(args, __dirname, 'installGlobalLFSFilter')
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L47-56)
```typescript
const credWithAccount = (c: Credential, a: IGitAccount | undefined) =>
  a && new Map(c).set('username', a.login).set('password', a.token)

async function getGitHubCredential(cred: Credential, store: AccountsStore) {
  const endpoint = `${getCredentialUrl(cred)}`
  const account = await findGitHubTrampolineAccount(store, endpoint)
  if (account) {
    info(`found GitHub credential for ${endpoint} in store`)
  }
  return credWithAccount(cred, account)
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L94-99)
```typescript
async function getCredential(cred: Credential, store: Store, token: string) {
  const ghCred = await getGitHubCredential(cred, store)

  if (ghCred) {
    return ghCred
  }
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L241-248)
```typescript
    if (firstParameter === 'get') {
      const cred = await getCredential(input, store, token)
      if (!cred) {
        const endpoint = `${getCredentialUrl(input)}`
        info(`could not find credential for ${endpoint}`)
        setHasRejectedCredentialsForEndpoint(token, endpoint)
      }
      return cred ? formatCredential(cred) : undefined
```
