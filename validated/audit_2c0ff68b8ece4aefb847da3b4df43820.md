Based on my investigation, the credential-helper trampoline matches credentials to a request purely by URL origin, with no binding to the git operation/remote that actually initiated the request: [1](#0-0) 

### Title
GitHub credential trampoline hands out stored account tokens to any redirected/mid-operation credential request matching origin, without binding to the operation's actual remote - (File: app/src/lib/trampoline/find-account.ts)

### Summary
`findGitHubTrampolineAccount` and the credential-helper handler decide whether to hand over a stored GitHub/GHE account token purely by comparing the *origin* of the URL that `git`'s credential-helper protocol reports, against the origin of a stored account's API endpoint [1](#0-0) . Nothing in this path checks that the request originates from the trampoline token/operation that was actually launched against that remote, nor that the URL matches the remote configured for the repository being cloned/fetched. This mirrors the reported bug class: a handler consumes an untrusted message (here, a `git credential-helper get` request coming from a spawned git subprocess) and acts on it without verifying it belongs to the context (repository/remote/operation) it claims to be for — analogous to `HandleReply` trusting `reply.GetThresholdKey()` without checking it equals `c.thresholdKey.Id`.

### Finding Description
`withTrampolineEnv`/`spawnGit` issue a fresh, short-lived `trampolineToken` per git invocation and register `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` in the child's environment [2](#0-1) . The trampoline server only validates that the token is *some* currently-live token (`isValidTrampolineToken`), not that it is scoped to the specific remote/host the command is being asked about [3](#0-2) [4](#0-3) .

When `git` (invoked as part of any clone/fetch/push, including sub-invocations triggered by a repo's own configuration, e.g. submodule URLs, LFS smudge/clean filters, or an HTTP redirect during the smart-HTTP handshake) calls the credential helper with a `get` request for some URL, `getGitHubCredential` looks up a matching account solely by comparing `new URL(getHTMLURL(a.endpoint)).origin === parsedUrl.origin` [1](#0-0) , and if it matches, immediately merges the stored account's username/token into the credential and returns it — no confirmation is made that this credential request is for the URL/remote that the *current* clone/fetch operation was started against [5](#0-4) . `getEndpointKind` similarly classifies a URL as `github.com`/`ghe.com`/`enterprise` from the presented URL/`WWW-Authenticate` header alone [6](#0-5) .

Because the trust decision is "does this origin match a stored account" rather than "is this credential request for the remote that this specific trampoline token's git process was launched against," any attacker who can get a spawned git process (launched during operations on an attacker-controlled repository) to issue a credential-helper `get` request whose URL's origin happens to equal `github.com`/a configured GHE endpoint will receive the user's live GitHub token — even though the repository/operation being processed is not the one associated with that account. Unlike the consortium-node bug's `c.thresholdKey.Id`, there's no equivalent "current operation's expected remote" field checked here.

### Impact Explanation
A successful trigger causes silent exfiltration of the user's GitHub/GHE OAuth token to a git subprocess that git itself will then use to authenticate an HTTP request to whatever host actually receives the connection (which, in a redirect or maliciously-configured submodule/LFS-endpoint scenario, need not be the real github.com). This is a credential/token exfiltration and unauthorized use-of-account primitive under the stated valid-impact criteria, entirely within an unprivileged attacker-controlled-repository threat model.

### Likelihood Explanation
Likelihood is moderate-to-low to fully weaponize without further verification: git itself has some protections around following cross-origin/cross-protocol redirects and credential-helper invocation is normally tied to the URL actually being fetched, so demonstrating a concrete redirect or submodule/LFS path that causes git to invoke the credential helper for `github.com` while the underlying network request goes to an attacker-controlled endpoint would require additional confirmation (e.g. testing git's `http.followRedirects` defaults, LFS smudge command construction) that I could not fully complete within the available tool budget. The structural gap — matching by origin only, without binding to the operation's originating remote/token context — is nonetheless clearly present and directly analogous to the reported missing-context-check bug class.

### Recommendation
Bind each `trampolineToken` to the specific remote URL(s)/host(s) the initiating git operation was started against (stored alongside `trampolineEnvironmentPath`/`isBackgroundTaskEnvironment` in `trampoline-environment.ts`), and have `getCredential`/`findGitHubTrampolineAccount` reject or re-prompt when the credential-helper's requested origin does not match one of the expected remotes for that token, instead of trusting any origin match found in the global accounts list.

### Proof of Concept
Conceptual (not independently executed against a live redirecting server):
1. User clones/fetches an attacker-controlled repository whose remote/`.gitmodules`/LFS configuration causes an additional outbound HTTP request during the operation.
2. That request, or a redirect encountered while satisfying it, presents a URL whose origin equals `https://github.com` (or the user's configured GHE endpoint) to git's credential-helper protocol.
3. `desktop`'s trampoline `CredentialHelper` handler receives the `get` command tagged with the *same* `trampolineToken` as the legitimate clone/fetch, calls `getGitHubCredential` → `findGitHubTrampolineAccount`, which matches purely on origin [1](#0-0) , and returns the user's real GitHub token via `credWithAccount` [7](#0-6) .
4. Git then authenticates the (possibly attacker-influenced) outbound request with that token.

### Citations

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

**File:** app/src/lib/trampoline/trampoline-tokens.ts (L13-16)
```typescript
/** Checks if a given trampoline token is valid. */
export function isValidTrampolineToken(token: string) {
  return trampolineTokens.has(token)
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-99)
```typescript
/** Implementation of the 'get' git credential helper command */
async function getCredential(cred: Credential, store: Store, token: string) {
  const ghCred = await getGitHubCredential(cred, store)

  if (ghCred) {
    return ghCred
  }
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-179)
```typescript
const getEndpointKind = async (cred: Credential, store: Store) => {
  const credentialUrl = getCredentialUrl(cred)
  const endpoint = `${credentialUrl}`

  if (isGist(endpoint)) {
    return 'generic'
  }

  if (isDotCom(endpoint)) {
    return 'github.com'
  }

  if (isGHE(endpoint)) {
    return 'ghe.com'
  }

  // When Git attempts to authenticate with a host it captures any
  // WWW-Authenticate headers and forwards them to the credential helper. We
  // use them as a happy-path to determine if the host is a GitHub host without
  // having to resort to making a request ourselves.
  for (const [k, v] of cred.entries()) {
    if (k.startsWith('wwwauth[')) {
      if (v.includes('realm="GitHub"')) {
        return 'enterprise'
      } else if (/realm="(GitLab|Gitea|Atlassian Bitbucket)"/.test(v)) {
        return 'generic'
      }
    }
  }

  const existingAccount = await findGitHubTrampolineAccount(store, endpoint)
  if (existingAccount) {
    return isDotCom(existingAccount.endpoint) ? 'github.com' : 'enterprise'
  }

  // All GitHub hosts use HTTPS, so if the protocol is not HTTPS we can
  // assume that this is not a GitHub host.
  if (credentialUrl.protocol !== 'https:') {
    return 'generic'
  }

  return (await isGitHubHost(endpoint)) ? 'enterprise' : 'generic'
}
```
