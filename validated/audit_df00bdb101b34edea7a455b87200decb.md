## Analog Found

### Title
Trampoline credential helper leaks a signed-in account's real OAuth token to any Git subprocess (filters/hooks) spawned during operations on a hostile repository - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The Solidity report's broken invariant is a *confused-deputy*: a privileged proxy (`Magnetar`) forwards a sensitive action using its own blanket approval instead of verifying that the actual caller is entitled to act on the specific resource. GitHub Desktop has the same shape of bug in its Git credential trampoline: any subprocess that Desktop spawns as part of a Git operation (including Git filters and hooks defined *inside the repository being cloned/fetched*) inherits the environment needed to talk to the trampoline TCP server, and the trampoline hands back the real signed-in account's OAuth/PAT for **any** URL whose origin matches a stored account - with no check that the request is scoped to the repository/account pairing that the operation is actually about.

### Finding Description
`withTrampolineEnv` (used by every remote Git operation) sets `DESKTOP_PORT` and `DESKTOP_TRAMPOLINE_TOKEN` and configures `GIT_CONFIG_PARAMETERS` to point `credential.helper` at the trampoline, explicitly so that **subprocesses spawned by Git filters (e.g. Git LFS) also pick up this configuration**: [1](#0-0) 

The token itself is only checked for validity, not scoped to a URL, repository, or account - `isValidTrampolineToken` merely confirms the token belongs to *some* currently running Desktop-initiated Git operation: [2](#0-1) [3](#0-2) 

When a `CREDENTIALHELPER get` command arrives, `getGitHubCredential` resolves the account purely by matching the *origin* of the requested URL against any signed-in account - not by checking which account/repository this specific operation is supposed to be scoped to - and unconditionally returns that account's real token: [4](#0-3) [5](#0-4) 

This mirrors `_processTapTokenOperation` forwarding `exerciseOption` for a `oTAP` the caller doesn't own: the deputy (trampoline) authorizes based on a coarse match (origin ↔ any stored account) rather than verifying that *this particular caller/operation* is entitled to *this particular* credential.

### Impact Explanation
A malicious repository can ship a `.gitattributes` filter/diff/merge driver (or, when hook interception is disabled/not applicable, a native hook such as `post-checkout`/`post-merge` via `core.hooksPath`) that is executed as a child process during an ordinary `clone`/`fetch`/`pull`/`checkout` performed through Desktop. That child process inherits `DESKTOP_PORT` and `DESKTOP_TRAMPOLINE_TOKEN` from the Git process environment. It can open a TCP connection to `127.0.0.1:$DESKTOP_PORT`, speak the trampoline protocol (`CREDENTIALHELPER get`, `url=https://github.com`), and receive back the currently signed-in user's real GitHub OAuth token/PAT - exfiltrating it outside the repo, with no interaction beyond the user opening/fetching the hostile repository.

### Likelihood Explanation
The environment variables required to reach the trampoline (`DESKTOP_PORT`, `DESKTOP_TRAMPOLINE_TOKEN`) are set for essentially every remote Git operation Desktop performs, and the comment in `trampoline-environment.ts` confirms this was deliberately designed so external filter processes (Git LFS and similar) inherit this configuration. Whether native repository hooks are also exposed depends on whether hook interception (`withHooksEnv`) is active for the given operation: [6](#0-5) 
When interception is disabled or not requested for an operation, native hooks run with the parent Git process's environment intact, giving them the same access. I was not able to fully confirm, from the indexed code alone, which specific Desktop-initiated git commands pass `interceptHooks`, so the hook-based path should be treated as likely but unverified, while the Git-filter path (explicitly called out in the source comment) is directly confirmed by code.

### Recommendation
Bind each trampoline token/session to the specific repository and account context that initiated it (e.g., only allow the credential helper to resolve credentials for the account already associated with the remote being operated on, not any signed-in account matching the URL's origin), and avoid exposing `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` to untrusted child processes (filters, hooks) spawned from within the repository's own tracked configuration.

### Proof of Concept
1. Attacker publishes a repository containing a `.gitattributes` with a custom filter (e.g. `filter=exfil` with `clean`/`smudge` commands) or a tracked `core.hooksPath` directory with an executable `post-checkout` hook.
2. Victim clones/fetches/checks out the repository in GitHub Desktop while signed in to a GitHub account.
3. During the Git operation, Desktop wraps the invocation via `withTrampolineEnv`, exposing `DESKTOP_PORT`/`DESKTOP_TRAMPOLINE_TOKEN` to the spawned Git process and its filter/hook subprocesses.
4. The attacker's filter/hook script connects to `127.0.0.1:$DESKTOP_PORT`, sends the `CREDENTIALHELPER get` command with `url=https://github.com`, and the trampoline (`getGitHubCredential` → `findGitHubTrampolineAccount`) returns the victim's real token, which the script exfiltrates over the network.

### Citations

**File:** app/src/lib/trampoline/trampoline-environment.ts (L123-145)
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
```

**File:** app/src/lib/trampoline/trampoline-tokens.ts (L1-37)
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

/**
 * Allows invoking a function with a short-lived trampoline token that will be
 * revoked right after the function finishes.
 *
 * @param fn Function to invoke with the trampoline token.
 */
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

**File:** app/src/lib/hooks/with-hooks-env.ts (L29-42)
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
```
