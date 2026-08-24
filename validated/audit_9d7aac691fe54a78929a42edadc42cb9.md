Based on the investigation, GitHub Desktop's trampoline credential-helper mechanism has a scoping weakness that is a structural analog to the Malt Finance bug: a privileged grant (there, an ERC20 approval; here, a live credential-vending token/config) is issued broadly for an entire operation but is not restricted to the specific narrow purpose it was meant for, so it can be consumed by an unintended party.

### Title
Trampoline Credential Helper Grants Any Subprocess of a Git Operation Access to Stored GitHub Credentials for Any Host, Not Just the Remote Being Operated On - (File: `app/src/lib/trampoline/trampoline-environment.ts`)

### Summary
When Desktop runs a git command that may need credentials, it calls `withTrampolineEnv`, which mints a trampoline token and injects `DESKTOP_PORT`, `DESKTOP_TRAMPOLINE_TOKEN`, and a global `credential.helper=desktop` into `GIT_CONFIG_PARAMETERS` for the entire git invocation. [1](#0-0) 
These environment variables and git config apply to the whole process tree spawned by that git command — including hooks, filters and submodule sub-invocations — not just to the single credential request the top-level command needed. [2](#0-1) 
The trampoline server only validates that the token is currently "live" (i.e., belongs to an in-flight operation), not which remote/host it was originally issued for. [3](#0-2) [4](#0-3) 
When the credential helper receives a `get` request, it resolves the account purely from the URL contained in the request payload via `findGitHubTrampolineAccount`, matching by origin only — with no check that the URL corresponds to the remote the top-level git command was invoked against. [5](#0-4) [6](#0-5) 

### Finding Description
This mirrors the report's broken invariant: `StabilizerNode` approves `Auction` for a fixed amount but `allocateArbRewards` doesn't consume it exactly, leaving an outstanding grant usable beyond its intended, narrow purpose. Here, Desktop "approves" (via env vars + git config) the entire subprocess tree of a git operation to reach the credential-vending trampoline server, but the actual authorization decision inside the server (`getCredential` → `findGitHubTrampolineAccount`) is not scoped to the specific remote/host that triggered the operation — it will hand back the caller's real GitHub token for whatever host string is presented in the `get` request. [7](#0-6) 

An attacker who controls a cloned/fetched repository can supply a git hook (`post-checkout`, `post-merge`, `post-commit`, etc.) or a Git LFS/submodule-invoked helper that is executed as a child process of the git command Desktop is running. Because `GIT_ASKPASS`/`credential.helper=desktop` and `DESKTOP_PORT`/`DESKTOP_TRAMPOLINE_TOKEN` are exported into the whole subprocess's environment (this is explicitly called out as intentional so that "commands invoked by filters ... are able to pick up our configuration"), the malicious hook can directly run `git credential fill` with `protocol=https` / `host=github.com` and receive the user's live GitHub Desktop OAuth token back over stdout, entirely independent of whatever remote the top-level operation was actually working with. [8](#0-7) 

### Impact Explanation
A hook/script controlled by an attacker-supplied repository (analogous to the "attacker controls a cloned/fetched repository" impact criterion) can exfiltrate the victim's real GitHub personal token for `github.com`/GHE regardless of the repository or remote in front of the user, by simply querying `git credential fill` with a spoofed target host during any operation Desktop performs against that repo (clone, fetch, pull, push). This is a credential exfiltration vulnerability with account takeover potential (the leaked token typically carries `repo`, `gist`, etc. scopes used by Desktop).

### Likelihood Explanation
Likelihood depends on two facts I could not fully confirm from the index: (1) whether Desktop already disables/sandboxes hook execution for freshly-cloned or newly-added repositories (e.g., via `core.hooksPath` overrides in `app/src/lib/hooks/get-repo-hooks.ts` / `with-hooks-env.ts`, which the search surfaced but whose contents I was unable to inspect in this session), and (2) whether any additional guard restricts the credential-helper's URL matching to the operation's own remote. If hooks are executed unmodified (as is git's default behavior on `checkout`/`merge`/`commit` and as several past Desktop CVEs about arbitrary hook execution suggest), then the token/env leakage path described here is directly reachable by nothing more than cloning and interacting with an attacker-authored repository — no local access, admin rights, or social engineering beyond "the user opened/cloned this repo" is required.

### Recommendation
- Scope the trampoline token (and/or the credential-helper's account lookup) to the specific remote URL that the top-level git command was invoked against, rejecting `get` requests for any other host.
- Avoid exporting `DESKTOP_PORT`/`DESKTOP_TRAMPOLINE_TOKEN`/`credential.helper=desktop` into the environment of hook processes specifically (as opposed to git's own internal filter/credential subprocesses), e.g. by stripping them via `core.hooksPath` wrapper or explicit env scrubbing before hook execution.
- As with the Malt Finance fix (reset unused approval), ensure the credential grant is revoked/narrowed immediately once the specific credential exchange it was meant for completes, rather than remaining valid and unscoped for the full lifetime/subprocess tree of the git operation.

### Proof of Concept
1. Attacker publishes a repository containing a `.git/hooks/post-checkout` (delivered via a template, or exploiting any code path that lets a fetched/cloned repo's hooks run) script that does:
   ```sh
   printf 'protocol=https\nhost=github.com\n' | git credential fill
   ```
2. Victim clones/fetches/checks out the repository in GitHub Desktop while signed in.
3. Desktop's `withTrampolineEnv` sets `DESKTOP_PORT`, `DESKTOP_TRAMPOLINE_TOKEN`, and `credential.helper=desktop` for the whole git invocation.
4. The hook process, inheriting this environment, connects to the live trampoline server and issues a `CREDENTIALHELPER get` command for `host=github.com`.
5. `createCredentialHelperTrampolineHandler` → `getCredential` → `getGitHubCredential` → `findGitHubTrampolineAccount` returns the victim's real GitHub account/token because the lookup only checks origin equality, not which remote the top-level operation targets. [9](#0-8) 
6. The hook writes the exfiltrated credential to a file or sends it over the network.

### Citations

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

**File:** app/src/lib/trampoline/trampoline-tokens.ts (L13-16)
```typescript
/** Checks if a given trampoline token is valid. */
export function isValidTrampolineToken(token: string) {
  return trampolineTokens.has(token)
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
