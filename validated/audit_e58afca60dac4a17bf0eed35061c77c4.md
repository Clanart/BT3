## Analysis

The C4 report's core bug pattern is: **a right-to-claim value is validated but never consumed/scoped**, so the same grant can be reused an unbounded number of times within a single check. The GitHub Desktop analog for this pattern is the **trampoline credential mechanism**, where a `DESKTOP_TRAMPOLINE_TOKEN` is treated as a single git-operation-wide "is a session active" flag rather than being scoped to *which credential/host it may be used to request*. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Untrusted git hooks/filters spawned during a fetch/clone can query arbitrary-host credentials through the live trampoline session - (File: app/src/lib/trampoline/trampoline-server.ts, app/src/lib/trampoline/trampoline-credential-helper.ts)

### Summary
`withTrampolineEnv` issues one `DESKTOP_TRAMPOLINE_TOKEN` for the *entire* duration of a git subprocess invocation (clone/fetch/push) and sets `GIT_CONFIG_PARAMETERS` to install `credential.helper=desktop` and `GIT_ASKPASS=''` for that whole invocation, then revokes the token only when the outer `fn` resolves. [4](#0-3) 
The trampoline server's only authorization check for any command received on the socket is `isValidTrampolineToken(command.trampolineToken)`, i.e. "is this token currently active", with no restriction on the identity of the caller, the number of calls, or the host being requested. [5](#0-4) 
The `CredentialHelper` handler resolves credentials purely from the `host`/`protocol` fields inside the credential request the client sends over the socket, matching it against the user's stored GitHub accounts, with no check that this host corresponds to the remote the outer git command is actually operating on. [6](#0-5) [7](#0-6) 

### Finding Description
Any git subprocess spawned as part of the outer operation — a Git hook (`post-checkout`, `post-merge`, `pre-push`, etc.), an LFS/`clean`/`smudge` filter, or a submodule's own recursive git invocation — inherits the same environment: `DESKTOP_TRAMPOLINE_TOKEN`, `GIT_ASKPASS`, and the `credential.helper=desktop` config. All of these are attacker-controlled content once the user has cloned or fetched a malicious repository (hooks are not executed automatically today, but LFS smudge filters and submodule URLs specified in `.gitmodules`/`.lfsconfig` are, and any script invoked in that context can freely call `git credential fill`). While the token is valid — for the whole duration of the fetch/clone — such a script can issue its own `git credential fill` request with `host=github.com` (or any GHE host the user is signed into), independent of the actual remote being cloned. Because the trampoline server's authorization is "token currently alive" rather than "this specific request belongs to this specific git operation/host", `getGitHubCredential` will look up and return the user's real GitHub.com/GHE PAT/OAuth token for that host, which the malicious script can then exfiltrate over the network. [8](#0-7) 

This mirrors the ReferralFeePoolV0 bug class exactly: the "right" being checked (`isValidTrampolineToken`) is a coarse, unscoped boolean state that is never reduced/consumed/bound to the specific claim (specific host/specific git invocation) it's supposed to authorize, so it can be reused repeatedly for anything within its validity window.

### Impact Explanation
A successful exploitation exfiltrates the user's real GitHub.com or GitHub Enterprise credentials (PAT/OAuth token) to an attacker-controlled endpoint, purely by the victim cloning/fetching a booby-trapped repository — no local access, no admin rights, and no unnatural extra steps beyond a normal clone/fetch/pull. This satisfies the "credential/token exfiltration" impact category from an attacker-controlled cloned/fetched repository.

### Likelihood Explanation
Likelihood is bounded by whether an unprivileged path exists to run arbitrary code during the trampoline window without hook execution (Desktop does not auto-run arbitrary hooks). The clearest reachable vector is Git LFS smudge/clean filters and submodule handling, both of which can run attacker-supplied commands as part of a normal `clone`/`fetch`/checkout while `GIT_ASKPASS`/`credential.helper=desktop` and the token are live in the environment. This is a somewhat narrower likelihood than a fully generic "any operation" trigger, but the underlying authorization gap (token not scoped to a single credential request/host) is a confirmed design property of `isValidTrampolineToken` / `processCommand`.

### Recommendation
Scope trampoline tokens to the specific remote/host (and ideally to a single expected credential request) rather than treating them as a coarse "session active" flag; reject `CredentialHelper` requests whose `host` does not match the remote URL the outer git operation was invoked against, and invalidate/consume the token after its first legitimate use rather than only at the end of the whole git invocation.

### Proof of Concept
1. Attacker publishes a repository containing a `.gitattributes` entry wiring a text file to an LFS-style smudge filter (or a `.gitmodules` submodule URL) that, when invoked during `git clone`/`git fetch`, runs a script.
2. Victim clones the repository in GitHub Desktop, which calls `withTrampolineEnv`, setting `DESKTOP_TRAMPOLINE_TOKEN` and `credential.helper=desktop` for the whole clone. [4](#0-3) 
3. While the token is still valid, the filter script runs `printf "protocol=https\nhost=github.com\n" | git credential fill`, using the inherited `GIT_CONFIG_PARAMETERS`/`DESKTOP_TRAMPOLINE_TOKEN` env, which routes to the trampoline server. [5](#0-4) 
4. `getGitHubCredential` looks up the user's stored GitHub.com account for that host (unrelated to the actual clone's remote) and returns username/token in the credential response. [6](#0-5) 
5. The filter script exfiltrates the returned token over the network before the git operation completes.

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

**File:** app/src/lib/trampoline/trampoline-server.ts (L162-183)
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

    if (result !== undefined) {
      socket.end(result)
    } else {
      socket.end()
    }
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-99)
```typescript
/** Implementation of the 'get' git credential helper command */
async function getCredential(cred: Credential, store: Store, token: string) {
  const ghCred = await getGitHubCredential(cred, store)

  if (ghCred) {
    return ghCred
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
