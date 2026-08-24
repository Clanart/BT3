### Title
Trampoline credential requests are authorized by a bare token check with no binding to the git operation's actual remote/account context - (File: `app/src/lib/trampoline/trampoline-server.ts`)

### Summary
The reported bug's root cause is a "signature" whose scope check omits the context it's supposed to be bound to (organization/contract), so a valid credential is replayable in a foreign context. GitHub Desktop's trampoline credential-helper protocol has the same shape: the only authorization performed before honoring a `git credential get` request is `isValidTrampolineToken(command.trampolineToken)` [1](#0-0) , a check that only verifies the token exists in a global `Set`, not that the request's `host`/`url` fields correspond to the specific remote/account the token was minted for.

### Finding Description
When Desktop performs any remote git operation, it mints a short-lived trampoline token and passes it (plus the callback port) as environment variables to the spawned `git` process: `DESKTOP_PORT`, `DESKTOP_TRAMPOLINE_TOKEN` [2](#0-1) . The comment in that same function explicitly states the credential helper is wired via `GIT_CONFIG_PARAMETERS` (not `-c`) specifically so that **subprocess filters invoked during the operation (e.g. Git LFS smudge/clean) also inherit the credential-helper configuration and thus the trampoline token/port** [3](#0-2) .

On the server side, any TCP client that connects with a *currently valid* token is trusted:
```
private async processCommand(socket: Socket, command: ITrampolineCommand) {
  if (!isValidTrampolineToken(command.trampolineToken)) {
    throw new Error('Tried to use invalid trampoline token')
  }
  ...
}
``` [1](#0-0) 

Tokens are simple random UUIDs stored in one process-wide `Set`, valid for the whole lifetime of the enclosing git operation [4](#0-3) , with **no binding to**: which remote/repository triggered the operation, which account is expected to be used, or which `host`/`url` the credential request is "supposed" to be for. The `host`/`protocol`/`url` fields used to decide *whose* credential to return are taken verbatim from the untrusted request body (`getCredentialUrl(cred)`) [5](#0-4) , and the handler then looks up a real, stored GitHub account whose endpoint origin matches that attacker-suppliable host: `getGitHubCredential` → `findGitHubTrampolineAccount` [6](#0-5) [7](#0-6) .

This mirrors the report's broken invariant exactly: the "signature" (trampoline token) authorizes *any* request for *any* host, instead of being scoped to the specific remote/account context of the git invocation it was minted for — just like the Story contract's signature validated only `msg.sender` and not the specific `StoryNFT` contract instance.

**Exploit path:** A malicious/compromised repository that Desktop clones or fetches can ship a `.gitattributes` entry that routes a tracked path through an existing, already-configured filter driver (this is precisely the Git LFS pattern the code comment calls out) or otherwise cause a subprocess to run during the checkout/fetch with the inherited environment. That subprocess — running with content/config supplied by the attacker's repository — can open a TCP connection to `127.0.0.1:$DESKTOP_PORT` using the still-valid `$DESKTOP_TRAMPOLINE_TOKEN` from its environment and issue a raw `CREDENTIALHELPER get` command whose `host`/`url` field names an arbitrary GitHub host the user is genuinely signed into (e.g. `github.com`), even though the actual git operation Desktop initiated was against the attacker's own remote. Because the server only checks token validity — not that the requested host matches the remote actually being operated on — `getCredential` will find the user's real stored account for that host and hand back `username`/`password` (the real OAuth token) to the attacker-controlled subprocess [8](#0-7) .

### Impact Explanation
This results in credential/token exfiltration of the user's real GitHub account token, obtainable by an attacker who only controls the content of a repository the victim clones or fetches (no local access, no prior malware, no leaked credentials, no social engineering — the request is a raw protocol call, not a UI prompt). This is a direct match for the "Valid Impact" category "attacker controls a cloned/fetched repository ... credential/token exfiltration."

### Likelihood Explanation
Likelihood is constrained by needing a git-invoked child process (filter/hook/credential-consuming tool) to run during the operation and be able to speak the trampoline wire protocol to `127.0.0.1:$DESKTOP_PORT`. I was not able to fully verify, purely from the indexed code, which exact filter/hook mechanisms are exercised automatically and unconditionally during a plain `clone`/`fetch` of an untrusted repository (e.g., whether Git LFS's `smudge`/`clean` filter is invoked before any user opt-in, or whether `.gitattributes`-declared filters require a pre-existing driver command already configured on the victim's machine). This nuance should be confirmed against the actual `withTrampolineEnv` call sites in `app/src/lib/git/core.ts` and Git LFS integration code, which weren't fully retrievable through the indexed search.

### Recommendation
Bind each trampoline token to the specific operation context it was minted for (e.g., the remote URL / expected account endpoint / repository) at `withTrampolineToken`/`withTrampolineEnv` time, and have `getCredential`/`getGitHubCredential` reject or scope-check requests whose `host`/`url` don't match the bound context for that token, analogous to including the "contract address" (here, the expected remote endpoint) in the trust decision rather than trusting the token alone.

### Proof of Concept
Not independently executable from the indexed code alone (would require constructing a malicious repository with a filter/hook payload and a raw socket client speaking the trampoline `\0`-delimited protocol to demonstrate the credential leak end-to-end); the exploit path is derived from static analysis of `trampoline-server.ts`, `trampoline-tokens.ts`, `trampoline-environment.ts`, and `trampoline-credential-helper.ts` as cited above, plus the code's own comment confirming that filter subprocesses inherit the trampoline credential-helper configuration.

### Citations

**File:** app/src/lib/trampoline/trampoline-server.ts (L162-166)
```typescript
  private async processCommand(socket: Socket, command: ITrampolineCommand) {
    if (!isValidTrampolineToken(command.trampolineToken)) {
      throw new Error('Tried to use invalid trampoline token')
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

**File:** app/src/lib/trampoline/trampoline-environment.ts (L122-147)
```typescript
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
