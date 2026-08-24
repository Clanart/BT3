### Title
Trampoline credential helper discloses GitHub account tokens for arbitrary hosts requested by any process holding the operation's trampoline token - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

### Summary
GitHub Desktop uses a "trampoline" token to let a Git subprocess (and anything it spawns, e.g. clean/smudge/LFS filters or hooks) call back into Desktop over a local TCP socket to fetch credentials for the current operation. The trampoline server only checks that the token is *valid* (i.e. issued for the currently running git command), not that the *credential URL* being requested corresponds to the actual remote of the repository/operation the token was created for. This is structurally the same flaw as the Centrifuge router: a component is granted broad trusted access ("endorsed operator"/valid token) for a specific legitimate purpose, but nothing checks that the specific action being requested is actually the one that principal was authorized to perform, letting it be used to act on behalf of an unrelated party.

### Finding Description
When Desktop spawns a Git process it wraps it with `withTrampolineEnv`, which mints a token via `withTrampolineToken` and stores it in `isValidTrampolineToken`'s global set ( [1](#0-0) ), and exports it to the child process as `DESKTOP_TRAMPOLINE_TOKEN` / `DESKTOP_PORT` ( [2](#0-1) ).

The trampoline server (`TrampolineServer.processCommand`) accepts any TCP client on that port and only validates the *token*, not the caller's identity, path, or the URL it's asking about: [3](#0-2) 

The registered credential-helper handler (`createCredentialHelperTrampolineHandler`) then resolves the "get" command by trusting whatever `url`/`host` is embedded in the request and matching it against the user's stored GitHub accounts by hostname only: [4](#0-3) [5](#0-4) 

Nowhere in `getCredential`/`getGitHubCredential`/`getGenericCredential` is the requested credential URL checked against the repository's own configured remote (the "owner" of the operation) — any process that can present the valid token can ask for credentials for **any** host, e.g. `https://github.com`, and if the user has a GitHub.com account signed into Desktop, `findGitHubTrampolineAccount` will happily return that account's real OAuth/PAT token via `credWithAccount` ( [6](#0-5) ).

Because the same token/port is shared for the whole Git invocation, and Git legitimately spawns filters, hooks, and LFS smudge/clean processes as children of that same invocation (which inherit `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT`), any attacker-controlled content that causes such a subprocess to run during a clone/fetch/checkout of a hostile repository — e.g. a `.gitattributes`-declared filter, or an `askpass`/`credential.helper` invocation triggered indirectly — can call `git credential fill` with `url=https://github.com` itself. Since the local socket accepts the request and only checks token validity, Desktop will hand back the user's actual github.com credentials to that attacker-triggered process, regardless of what remote the current operation is actually targeting. This mirrors the router bug exactly: the trusted party (`isValidTrampolineToken` ≈ `isOperator[owner][router]`) is authorized broadly, but the specific request's target (arbitrary `url`/`host` ≈ arbitrary `owner`/`controller`) is never checked against what that authorization was meant to cover.

### Impact Explanation
Successful exploitation discloses the user's real GitHub.com/GHE OAuth token (or generic git credentials) to a process spawned in the context of cloning/fetching/checking out an attacker-controlled or attacker-influenced repository. This is credential/token exfiltration — a Critical-class impact matching the reported bug class: it lets an untrusted repository obtain and exfiltrate the real account's access token without the account owner's consent, going far beyond the intended scope (which is to answer Git's own auth prompt for the operation's actual remote).

### Likelihood Explanation
Requires only that the victim clone/fetch a repository that can cause a subprocess to run during the Git operation while a trampoline token is live (e.g. via a smudge/clean filter, LFS custom transfer agent, or hook if hooks-in-shell-env is enabled) and that the victim has a GitHub.com/GHE account already signed in to Desktop — a very common, unprivileged setup. No special local access, admin rights, or social engineering beyond "clone/open a hostile repo" (already inside the allowed threat model) is needed.

### Recommendation
Scope trampoline credential responses to the operation they were issued for: when minting the token in `withTrampolineEnv`, record the expected remote/host(s) for that operation (in addition to `trampolineEnvironmentPath`), and in `getCredential`/`getGitHubCredential`/`getGenericCredential` reject (or re-prompt/warn) if `getCredentialUrl(cred)`'s host does not match the recorded host for that token, analogous to requiring `owner == _initiator()` in the Centrifuge fix. At minimum, do not hand out first‑party GitHub account tokens for hosts unrelated to the git remote(s) configured for the path stored via `trampolineEnvironmentPath`.

### Proof of Concept
1. Sign in to a GitHub.com account in Desktop.
2. Prepare a malicious repository with a `.gitattributes` filter (or LFS custom transfer agent) that, when invoked as part of `git clone`/`checkout`, runs a script reading `DESKTOP_PORT`/`DESKTOP_TRAMPOLINE_TOKEN` from its environment and performs the Git credential protocol manually:
   ```
   printf 'protocol=https\nhost=github.com\n\n' | nc 127.0.0.1 $DESKTOP_PORT
   ```
   framed per `ITrampolineCommand`/`TrampolineCommandParser` (identifier `CREDENTIALHELPER`, action `get`, using `DESKTOP_TRAMPOLINE_TOKEN`).
3. Clone/open this repository in Desktop, triggering the filter during the clone.
4. The trampoline server validates only the token (`isValidTrampolineToken`) and returns the user's real github.com credentials via `getGitHubCredential`, which the filter script exfiltrates over the network.

Note: I was unable to fully trace whether `interceptHooks`/`with-hooks-env.ts` restricts which processes see `DESKTOP_TRAMPOLINE_TOKEN` (that env var is separate from the `PROCESS_PROXY_TOKEN` used for the hooks proxy), so the exact triggering subprocess (filter vs. hook vs. LFS agent) should be confirmed with a live PoC; the missing URL/host validation in the credential handler itself, however, is directly confirmed in the cited source.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L47-57)
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
