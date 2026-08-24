## Title
Trampoline credential-helper token is not bound to the target remote, allowing a malicious repository's spawned process to exfiltrate the user's GitHub token during an authenticated clone/fetch - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
GitHub Desktop authenticates Git operations via a "trampoline": a short‑lived `DESKTOP_TRAMPOLINE_TOKEN` and `DESKTOP_PORT` are exported into the environment of the spawned `git` process, and Git is configured to call back into Desktop's TCP `TrampolineServer` (via `credential.helper=desktop` / `GIT_ASKPASS`) whenever it needs credentials. The token only proves that *some* process belongs to the currently-running git operation - it carries no binding to the specific host/remote that operation is supposed to authenticate against. This is structurally the same defect as the reported Story Protocol bug: a signature/credential is used only to "whitelist" a channel, while the actual target of the privileged action is supplied later, by whoever holds the channel, and is never checked against what was originally authorized.

### Finding Description
`withTrampolineEnv` (`app/src/lib/trampoline/trampoline-environment.ts:93-147`) generates a single trampoline token via `withTrampolineToken` and injects it, together with the trampoline server port, into the environment used to spawn the `git` process: [1](#0-0) 

`isValidTrampolineToken` (`app/src/lib/trampoline/trampoline-tokens.ts:13-16`) only checks that the token exists in a global `Set` - it is not scoped to a repository, host, or remote URL: [2](#0-1) 

When the trampoline server receives a `get` command, `getGitHubCredential` looks up the account purely from the `url`/`host` fields present in the *credential request itself*: [3](#0-2) [4](#0-3) 

Nothing in this path checks that the `host`/`url` supplied in the credential request matches the remote that the outer, user-initiated git operation (clone/fetch/push) is actually targeting. The token is a bearer credential valid for the whole lifetime of the git operation, for *any* credential request, for *any* host - it is analogous to the report's `setBatchPermissionForModules()` signature, which whitelists a contract to call a module but does not bind the call to specific parameters.

`DESKTOP_TRAMPOLINE_TOKEN` and `DESKTOP_PORT` are environment variables, and environment variables are inherited by every child process the `git` invocation spawns - filters (`clean`/`smudge`), remote helpers, and Git LFS custom transfer agents, all of which can be configured by a repository's own tracked files (`.gitattributes`, `.lfsconfig`, `.git/config` fetched via clone, etc.) and thus by a malicious/compromised repository the user is simply cloning or fetching. Any such attacker-controlled child process can independently connect to the trampoline server on `127.0.0.1:<DESKTOP_PORT>` (the protocol is a simple line-based socket, see `vendor/desktop-trampoline/README.md:1-80` and `app/src/lib/trampoline/trampoline-server.ts:1-98`) and issue its own `get` request with `host=github.com`/`protocol=https`, independent of whatever remote the user's actual git command is authenticating to. [5](#0-4) 

Because `getGitHubCredential` resolves credentials solely from the account's endpoint matching the requested URL's origin, this rogue request returns the user's real GitHub.com/GHE OAuth token in the clear: [6](#0-5) 

### Impact Explanation
A successful exploitation yields the plaintext OAuth token of the signed-in GitHub account, exfiltrated to an attacker-controlled process spawned merely by the act of cloning/fetching/checking out the attacker's repository (which triggers filters/LFS/remote helpers). This satisfies the "Valid Impact" bar: the attacker controls a cloned/fetched repository, and the outcome is credential/token exfiltration, comparable in severity to the original report's arbitrary/unauthorized action executed via a hijacked authorization channel.

### Likelihood Explanation
The precondition is simply that the user clones or fetches from an attacker-supplied repository (or has such a remote configured) while signed in to a GitHub account in Desktop - no elevated privileges, no pre-existing malware, and no unnatural user action beyond a normal clone/fetch that Desktop explicitly supports. The mechanics needed (Git filters, `.lfsconfig`-driven LFS transfer agents, or "ext::"-style remote helpers configured within the repository's own tracked config) are standard, well-documented Git extension points, so the attacker only needs to craft repository content, not exploit any additional bug in Desktop's Git invocation itself. The main uncertainty is exactly which extension point Desktop's git invocation permits to run during a given operation (this could not be fully confirmed from the indexed files, e.g. whether `core.hooksPath`/filter execution is disabled for clone-only flows); this needs verification in a live Desktop session.

### Recommendation
Bind trampoline credential requests to the specific remote/host that the outer git operation is actually targeting: pass the expected host(s) into `withTrampolineEnv`/`withTrampolineToken`, store it alongside `isBackgroundTaskEnvironment`/`trampolineEnvironmentPath` keyed by token, and have `getCredential`/`getGitHubCredential` reject (or require prompting) requests whose `host`/`url` doesn't match the operation's expected remote. Alternatively/additionally, scope trampoline tokens to a single credential request (one-time-use) rather than the whole duration of the git process tree, and audit whether Git filter/LFS/remote-helper execution can be constrained during unprivileged clone/fetch of untrusted repositories.

### Proof of Concept
Conceptual PoC (not verified end-to-end due to the ask-only/read-only nature of this investigation):
1. Attacker publishes a repository containing a `.gitattributes` entry that routes a tracked file through a custom `clean`/`smudge` filter, or a `.lfsconfig` pointing Git LFS at a custom transfer agent script.
2. The victim, signed in to GitHub Desktop with a GitHub.com account, clones/fetches this repository through Desktop.
3. During the operation, Desktop spawns `git` with `DESKTOP_TRAMPOLINE_TOKEN` and `DESKTOP_PORT` in its environment (`app/src/lib/trampoline/trampoline-environment.ts:123-146`); the malicious filter/transfer-agent script inherits these env vars as a child process of `git`.
4. That script connects to `127.0.0.1:<DESKTOP_PORT>` using the trampoline wire protocol and sends a `credential-helper get` command with `host=github.com`, `protocol=https`, reusing the inherited `DESKTOP_TRAMPOLINE_TOKEN`.
5. `isValidTrampolineToken` accepts the token (it only checks set membership, not scope) and `getGitHubCredential`/`findGitHubTrampolineAccount` returns the victim's real GitHub account credentials (`app/src/lib/trampoline/trampoline-credential-helper.ts:50-57`, `app/src/lib/trampoline/find-account.ts:20-29`), which the script exfiltrates over the network.

### Citations

**File:** app/src/lib/trampoline/trampoline-environment.ts (L123-146)
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
```

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

**File:** app/src/lib/trampoline/trampoline-server.ts (L12-25)
```typescript
/**
 * This class represents the "trampoline server". The trampoline is something
 * we'll hand to git in order to communicate with Desktop without noticing. A
 * notable example of this would be GIT_ASKPASS.
 *
 * This server is designed so that it will start lazily when the app performs a
 * remote git operation. At that point, the app will try to retrieve the
 * server's port, which will run the server first if needed.
 *
 * The idea behind this is to simplify the retry approach in case of error:
 * instead of reacting to errors with an immediate retry, the server will remain
 * closed until the next time the app needs it (i.e. in the next git remote
 * operation).
 */
```
