## Title
Fake `WWW-Authenticate: realm="GitHub"` header on a non-GitHub remote tricks Desktop into treating the host as GitHub Enterprise, triggering an Enterprise sign-in flow scoped to the attacker's host - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind` uses the `wwwauth[]` headers that `git` forwards from the remote server as a "happy path" to decide whether a host is a GitHub Enterprise instance, without any host validation. [1](#0-0) 

### Finding Description
When `getCredential` can't find a stored GitHub account for the requested endpoint, it calls `getEndpointKind`, which inspects `wwwauth[]` entries forwarded by git for the credential request. If any header value contains `realm="GitHub"`, the endpoint is unconditionally classified as `'enterprise'`, regardless of the actual hostname, TLS certificate, or any GitHub API probe. [2](#0-1) 

Because these `wwwauth[]` values originate from the HTTP response of whatever server git is talking to (i.e., are attacker-controlled when the user has added/cloned from an attacker-run remote, or when it is reached over an insecure/interceptable transport), an attacker-controlled git server can respond with `WWW-Authenticate: realm="GitHub"` on any URL to force `endpointKind` to `'enterprise'` for a completely unrelated host.

Once classified as `'enterprise'` and no existing account matches that endpoint, `getCredential` calls `ui.promptForGitHubSignIn(endpoint)`, which opens the real GitHub Enterprise sign-in popup and sets `credentialHelperUrl`/`setSignInEndpoint` to the attacker's endpoint/origin: [3](#0-2) [4](#0-3) 

If the user completes this sign-in dialog (naturally believing this is a legitimate GHE prompt since it looks like Desktop's normal enterprise sign-in UI, and the URL shown is the actual remote URL they just added), the resulting `Account`'s login/token is merged onto the credential object via `credWithAccount` and returned to git as Basic Auth credentials for use against **the attacker's host**, not the real enterprise server the account belongs to. [5](#0-4) [6](#0-5) 

### Impact Explanation
If a user is convinced to sign in through this flow, git will subsequently send the user's real GitHub token (or an OAuth-derived Enterprise token) as Basic Auth credentials directly to the attacker-controlled host — this is a credential exfiltration path.

### Likelihood Explanation
Exploitation requires the victim to actively complete a sign-in prompt after adding/fetching from an attacker-controlled remote, and the sign-in flow itself performs a real GitHub OAuth/device flow (the popup does show the actual target URL as `credentialHelperUrl`), which gives the user a chance to notice something is off. I was not able to fully verify within the available context whether `beginEnterpriseSignIn`/`setSignInEndpoint` performs any additional host verification (e.g., checking that the entered endpoint is actually a reachable GHES `/meta` endpoint) before completing sign-in, which could reduce or eliminate the practical exploitability of forcing a token to be issued for a fully arbitrary host. This should be verified with a live/dynamic test before treating this as conclusively exploitable.

### Recommendation
Do not trust `wwwauth[]` realm claims alone to classify a host as GitHub/Enterprise. At minimum, corroborate the `realm="GitHub"` header with an actual API probe (the existing `isGitHubHost(endpoint)` fallback already does this) or require confirmation of the GHES `/meta` fingerprint before allowing the sign-in flow to associate a real GitHub credential with an arbitrary attacker-supplied endpoint.

### Proof of Concept
1. Set up a git server (or MITM proxy for an `http://` remote) that responds to Basic Auth challenges with `WWW-Authenticate: Basic realm="GitHub"`.
2. Add this server as a remote in GitHub Desktop and trigger a fetch/push so git invokes the credential helper.
3. `getEndpointKind` classifies the host as `'enterprise'` purely from the header content.
4. Desktop presents the "Sign in to GitHub Enterprise" dialog; if the user completes it, the resulting token is handed back to git as Basic Auth credentials for the attacker's server.

**Caveat:** I could not fully confirm end-to-end (e.g., whether `beginEnterpriseSignIn`/`setSignInEndpoint` in the dispatcher/sign-in store perform host-verification steps that would block or warn on a non-genuine GHES endpoint) due to index/context limits. A Devin session with full repo access would be needed to trace `dispatcher.beginEnterpriseSignIn` and the sign-in store to conclusively determine whether this results in actual token issuance/exfiltration to the attacker's host.

### Citations

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L94-125)
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
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-170)
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
```

**File:** app/src/lib/trampoline/trampoline-ui-helper.ts (L80-99)
```typescript
  public promptForGitHubSignIn(endpoint: string): Promise<Account | undefined> {
    return new Promise<Account | undefined>(async resolve => {
      const cb = (result: SignInResult) => {
        resolve(result.kind === 'success' ? result.account : undefined)
        this.dispatcher.closePopup(PopupType.SignIn)
      }

      const { hostname, origin } = new URL(endpoint)
      if (hostname === 'github.com') {
        this.dispatcher.beginDotComSignIn(cb)
      } else {
        this.dispatcher.beginEnterpriseSignIn(cb)
        await this.dispatcher.setSignInEndpoint(origin)
      }

      this.dispatcher.showPopup({
        type: PopupType.SignIn,
        isCredentialHelperSignIn: true,
        credentialHelperUrl: endpoint,
      })
```
