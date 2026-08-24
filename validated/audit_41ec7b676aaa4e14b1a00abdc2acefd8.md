### Title
Trampoline credential helper hands out any stored Git account's token to any process sharing the trampoline session, regardless of which host that process actually needs to talk to - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts](app/src/lib/trampoline/trampoline-credential-helper.ts))

### Summary
The original report's root cause is a confused-deputy pattern: a sensitive action (transferring the caller's funds) is authorized using an *ambient, implicit* signal (`tx.origin`) instead of verifying that the actual operation being performed was the one the authorized party intended. GitHub Desktop's Git credential trampoline has the same structural flaw: possession of the session-scoped `DESKTOP_TRAMPOLINE_TOKEN` is treated as sufficient authorization for *any* credential request that arrives on that channel, no matter which host the request names, and no matter whether that host has anything to do with the git operation the user actually initiated.

### Finding Description
When Desktop runs a Git network operation it wraps it in `withTrampolineEnv`, which injects `DESKTOP_TRAMPOLINE_TOKEN` and configures `credential.helper=desktop` via `GIT_CONFIG_PARAMETERS` specifically so that it is inherited by *child processes launched by Git itself*, such as Git LFS smudge/clean filters — the code comment is explicit about this being intentional: [1](#0-0) .

Every process that inherits this environment (the main Git process, and any filter/helper it spawns) can independently invoke the `credential` sub-protocol, and the trampoline server dispatches those requests to `createCredentialHelperTrampolineHandler`, which parses the free-form stdin sent by the caller (protocol/host/username fields fully controlled by whoever is invoking `git credential fill`) via `getCredentialUrl`: [2](#0-1) .

`getCredential` first tries `getGitHubCredential`, which looks up a stored account purely by matching the **hostname the caller claims to want** against the account's endpoint origin, with no check that this host is related to the actual repository/remote the trampoline token was created for: [3](#0-2)  using `findGitHubTrampolineAccount`: [4](#0-3) . If matched, the real username/token is returned directly: [5](#0-4) .

The trampoline does track a `trampolineEnvironmentPath` per token (via `getTrampolineEnvironmentPath`) [6](#0-5) , but this value is only used to invoke an *external* credential helper — it is never used to validate that the `host` a caller is asking about corresponds to a remote actually configured in that repository. There is no equivalent of "verify the signer/target matches the request" anywhere in `getCredential`/`getEndpointKind`: [7](#0-6) .

Because Git operations that Desktop performs (clone, checkout, submodule update) routinely trigger execution of Git LFS filters/hooks with this same inherited environment — Desktop's own changelog documents this integration ("Provide credentials to LFS repositories when performing checkout") — and because LFS endpoint/transfer configuration can be influenced by content that ships inside the repository being cloned (e.g. `.lfsconfig`), a malicious repository can cause an LFS filter invocation to request `git credential fill` for a **host of the attacker's choosing** (e.g. `github.com`) that has nothing to do with the actual clone URL, and receive back the user's real, stored token for that host.

### Impact Explanation
If exploited, this results in exfiltration of the victim's real GitHub.com/GitHub Enterprise access token (or any other Git host credential Desktop has stored) to a process controlled by the content of an untrusted repository the user simply cloned or fetched — the same class of impact the original report flags as high severity (unauthorized use of a party's credentials/funds triggered by content the victim didn't knowingly authorize for that purpose). A leaked GitHub token can be used to read/write private repositories, and in git-credential-manager configurations may grant broader account access.

### Likelihood Explanation
The attacker only needs the victim to clone/fetch/checkout a repository they control (or a legitimate-looking fork/mirror) — squarely within the "attacker controls a cloned/fetched repository" impact class. No local access, admin rights, or social engineering beyond a normal clone is required. The main uncertainty is the exact mechanism by which the LFS/filter subprocess can be made to request a credential host of the attacker's choosing (dependent on external Git LFS behavior, which is outside this repository), which is why this should be validated end-to-end with a live LFS proof of concept before being treated as fully confirmed against the actual runtime.

### Recommendation
Bind each `DESKTOP_TRAMPOLINE_TOKEN` session to the specific remote/host(s) legitimately associated with the git operation it was created for (available via `trampolineEnvironmentPath`/the initiating remote URL), and reject or prompt-confirm credential requests for hosts outside that set instead of unconditionally returning stored GitHub/Enterprise tokens for any hostname a caller names. Consider also scoping which named credential helper hostnames Git LFS filters running under `credential.helper=desktop` are allowed to request, mirroring the "provided signatures must match the actor performing the action" recommendation from the original report.

### Proof of Concept
1. Attacker publishes a public repository containing `.gitattributes` marking a file as LFS-tracked and an `.lfsconfig` (or LFS "custom transfer agent" configuration) that causes the LFS smudge/clean process to issue a `git credential fill` request with `host=github.com` (a host unrelated to the actual clone URL of the malicious repo).
2. Victim uses GitHub Desktop, which is already signed in to github.com, to clone or check out this repository. Desktop invokes `checkoutBranch`/`clone` with `envForRemoteOperation`, which flows through `withTrampolineEnv`, setting `DESKTOP_TRAMPOLINE_TOKEN` and `GIT_CONFIG_PARAMETERS` (credential.helper=desktop) for the whole operation, inherited by the LFS filter child process.
3. The LFS filter process calls `git credential fill` for `host=github.com`; this reaches `createCredentialHelperTrampolineHandler` → `getGitHubCredential` → `findGitHubTrampolineAccount`, which matches the victim's real GitHub.com account by hostname alone and returns the login/token.
4. The malicious LFS filter (an attacker-controlled executable/script invoked as part of the LFS transfer) forwards the returned credential to an attacker-controlled server, exfiltrating the victim's GitHub token — all triggered solely by cloning the untrusted repository.

### Citations

**File:** app/src/lib/trampoline/trampoline-environment.ts (L38-41)
```typescript
const trampolineEnvironmentPath = new Map<string, string>()

export const getTrampolineEnvironmentPath = (trampolineToken: string) =>
  trampolineEnvironmentPath.get(trampolineToken) ?? process.cwd()
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

**File:** app/src/lib/trampoline/trampoline-environment.ts (L126-143)
```typescript
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
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L47-48)
```typescript
const credWithAccount = (c: Credential, a: IGitAccount | undefined) =>
  a && new Map(c).set('username', a.login).set('password', a.token)
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L94-179)
```typescript
async function getCredential(cred: Credential, store: Store, token: string) {
  const ghCred = await getGitHubCredential(cred, store)

  if (ghCred) {
    return ghCred
  }

  const endpointKind = await getEndpointKind(cred, store)
  const accounts = await store.getAll()

  const endpoint = `${getCredentialUrl(cred)}`
  const apiEndpoint = getAPIEndpoint(endpoint)

  // If it appears as if the endpoint is a GitHub host and we don't have an
  // account for that endpoint then we should prompt the user to sign in.
  if (
    endpointKind !== 'generic' &&
    !accounts.some(a => a.endpoint === apiEndpoint)
  ) {
    if (getIsBackgroundTaskEnvironment(token)) {
      debug('background task environment, skipping prompt')
      return undefined
    }

    const account = await ui.promptForGitHubSignIn(endpoint)

    if (!account) {
      setHasRejectedCredentialsForEndpoint(token, endpoint)
    }

    return credWithAccount(cred, account)
  }

  // GitHub.com/GHE creds are only stored internally
  if (endpointKind !== 'generic') {
    return undefined
  }

  return useExternalCredentialHelper()
    ? getExternalCredential(cred, token)
    : getGenericCredential(cred, token)
}

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
