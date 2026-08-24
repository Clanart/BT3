### Title
Spoofable `WWW-Authenticate` realm allows an attacker-controlled git server to trigger unauthenticated GitHub/Enterprise sign-in binding on an arbitrary host - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

### Summary
`getEndpointKind()` in the trampoline credential helper classifies a remote host as GitHub Enterprise (`'enterprise'`) whenever the `WWW-Authenticate` header returned by that host during a git HTTP authentication challenge contains the substring `realm="GitHub"`, bypassing the safer network-verification path (`isGitHubHost()`) that is normally used to confirm a host actually is GitHub. This classification decision is made purely on attacker-controlled response content from the remote/proxy the user is cloning/fetching/pushing to.

### Finding Description
This is the same class of bug as the reported Solidity issue: a security-relevant state transition (there: "is this identity already seeded", here: "is this host a trusted GitHub host") is derived from a value the caller can fully control, and that classification result feeds a privileged code path that assumes trust.

In `getEndpointKind()`: [1](#0-0) 

The relevant heuristic: [2](#0-1) 

Git forwards any `WWW-Authenticate` response headers it receives from the remote server to the credential helper as `wwwauth[]` fields (this is documented in the code comment). GitHub Desktop's credential helper trusts the string `realm="GitHub"` in that header as sufficient proof that the remote is a GitHub Enterprise instance, and returns `'enterprise'` immediately — **without** falling through to `isGitHubHost()`, which is the function that actually attempts to verify GitHub-ness via a `/meta` request and checks for the `x-github-request-id` response header: [3](#0-2) 

That verified path is only reached for hosts that don't send a spoofed `wwwauth[]` header — i.e. the attacker's forged header is a shortcut around the app's own trust check.

The classification result is then used in `getCredential()`: [4](#0-3) 

Because `endpointKind !== 'generic'` and no stored account matches this brand-new attacker endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)`, which drives the UI to begin a GitHub Enterprise sign-in flow scoped to the attacker's `endpoint`/origin: [5](#0-4) 

This calls `dispatcher.beginEnterpriseSignIn(cb)` and `dispatcher.setSignInEndpoint(origin)` where `origin` is the attacker-controlled host, then shows a `SignIn` popup with `isCredentialHelperSignIn: true` and `credentialHelperUrl: endpoint` set to the attacker's URL. The corresponding OAuth logic resolves the OAuth code exchange against whatever `endpoint` was set: [6](#0-5) 

**The broken invariant:** "host classified as `enterprise`" is supposed to mean "verified GitHub Enterprise host," but it can instead mean "any host that echoed a crafted `WWW-Authenticate` header." Existing guards (`isGist`, `isDotCom`, `isGHE`, and ultimately `isGitHubHost`'s network probe) exist precisely to make this determination safely, but the `wwwauth[]` shortcut lets the remote self-declare its trust tier and skip them entirely.

### Impact Explanation
An attacker who controls (or can MITM/proxy) a git HTTP remote that the victim clones, fetches from, or pushes to can respond to the authentication challenge with a header such as `WWW-Authenticate: Basic realm="GitHub"`. This causes GitHub Desktop to:
1. Treat the attacker's arbitrary host as a legitimate GitHub Enterprise endpoint.
2. Surface a "sign in" UI targeting the attacker's origin, which is indistinguishable from a legitimate first-time GHE sign-in prompt.
3. If the user completes the sign-in (PAT entry or OAuth), begin the OAuth/token exchange against the attacker's `endpoint`, which can result in the user's GitHub Enterprise Server credentials or PAT being sent to the attacker's server, and/or a bogus `Account` (endpoint = attacker host) being persisted via `AccountsStore`/keychain (`TokenStore`), which subsequently causes Desktop to auto-supply whatever token gets bound there for all future requests to that attacker origin (`findGitHubTrampolineAccount`).

This maps to "unauthorized OAuth or account binding" and "credential/token exfiltration" in the accepted impact list, and the attacker primitive required (control of a git remote/proxy HTTP response) matches the accepted threat model exactly.

### Likelihood Explanation
Any git HTTP(S) operation (clone/fetch/push/fetch-in-background) against a server the attacker controls, or a network position that can inject/modify the `WWW-Authenticate` response header (no TLS termination needed if the attacker owns the server, or a MITM against a repo the user is told/tricked into adding as a remote), triggers this path automatically the first time Desktop needs credentials for that host — no special local access, malware, or existing credentials are required from the attacker. The only user action needed is normal use of GitHub Desktop against an untrusted remote (a routine, expected user action, not an "unnatural" or contrived step), and optionally completing the resulting sign-in prompt.

### Recommendation
Do not trust the `wwwauth[]`/`realm="GitHub"` header as sufficient proof of GitHub-Enterprise-ness for granting `'enterprise'` classification. At minimum:
- Require the network-verified `isGitHubHost()` check (or equivalent, e.g. checking for the `x-github-request-id` header/`x-github-enterprise-version` on an actual response) before ever offering the GitHub Enterprise sign-in flow, even when a `wwwauth[]` header claims `realm="GitHub"`.
- If the header is kept only as a UX hint (e.g., to pre-select "GitHub Enterprise" in a manual sign-in flow), make clear that this classification is unverified and never automatically bind newly obtained credentials to that host without an explicit, clearly-labeled trust confirmation step from the user (e.g. surfacing the raw host and warning that it's unverified).

### Proof of Concept
1. Stand up an HTTP server that responds to any request with `401 Unauthorized` and header `WWW-Authenticate: Basic realm="GitHub"`.
2. In GitHub Desktop, clone (or add as remote and fetch) a repo whose remote URL points at this attacker server, e.g. `https://evil.example.com/foo/bar.git`.
3. Git performs the HTTP request, receives the 401 + spoofed header, and invokes the trampoline credential helper's `get` command, forwarding the header as a `wwwauth[0]=Basic realm="GitHub"` stdin field.
4. `getEndpointKind()` matches `realm="GitHub"` and returns `'enterprise'` without ever calling `isGitHubHost('https://evil.example.com')`.
5. Since no account exists for `evil.example.com`, `getCredential()` invokes `ui.promptForGitHubSignIn('https://evil.example.com/...')`, presenting a GitHub Enterprise sign-in dialog scoped to the attacker's host.
6. Completing that sign-in flow (PAT entry or OAuth) sends the user's authentication material to `evil.example.com`, and/or persists an `Account` bound to that origin in `AccountsStore`, which will subsequently auto-supply credentials for it via `findGitHubTrampolineAccount`.

Note: I was not able to execute this against a live build to confirm the exact resulting UI copy/OAuth request shape (e.g., whether the PAT-entry vs. OAuth branch is offered for an unknown enterprise host) — this would require running Desktop's sign-in flow interactively, which is outside what static code review can confirm. The code path described above (`getEndpointKind` → `promptForGitHubSignIn` → `beginEnterpriseSignIn`/`resolveOAuthRequest`) is confirmed from source, but the full downstream UI/OAuth wiring for a never-before-seen enterprise endpoint would benefit from a running-app verification via a Devin session with browser/terminal access.

### Citations

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

**File:** app/src/lib/api.ts (L2429-2491)
```typescript
/**
 * Attempts to determine whether or not the url belongs to a GitHub host.
 *
 * This is a best-effort attempt and may return `undefined` if encountering
 * an error making the discovery request
 */
export async function isGitHubHost(url: string) {
  const { hostname } = new window.URL(url)

  const endpoint =
    hostname === 'github.com' || hostname === 'api.github.com'
      ? getDotComAPIEndpoint()
      : getEnterpriseAPIURL(url)

  if (isDotCom(endpoint) || isGHE(endpoint)) {
    return true
  }

  if (isKnownThirdPartyHost(hostname)) {
    return false
  }

  // github.example.com,
  if (/(^|\.)(github)\./.test(hostname)) {
    return true
  }

  // bitbucket.example.com, etc
  if (/(^|\.)(bitbucket|gitlab)\./.test(hostname)) {
    return false
  }

  if (getEndpointVersion(endpoint) !== null) {
    return true
  }

  // Add a unique identifier to the URL to make sure our certificate error
  // supression only catches this request
  const metaUrl = `${endpoint}/meta?ghd=${crypto.randomUUID()}`

  const ac = new AbortController()
  const timeoutId = setTimeout(() => ac.abort(), 2000)
  suppressCertificateErrorFor(metaUrl)
  try {
    const response = await fetch(metaUrl, {
      headers: { 'user-agent': getUserAgent() },
      signal: ac.signal,
      credentials: 'omit',
      method: 'HEAD',
      redirect: 'error',
    })

    tryUpdateEndpointVersionFromResponse(endpoint, response)

    return response.headers.has('x-github-request-id')
  } catch (e) {
    log.debug(`isGitHubHost: failed with endpoint ${endpoint}`, e)
    return undefined
  } finally {
    clearTimeout(timeoutId)
    clearCertificateErrorSuppressionFor(metaUrl)
  }
}
```

**File:** app/src/lib/trampoline/trampoline-ui-helper.ts (L80-104)
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
    }).catch(e => {
      log.error(`Could not prompt for GitHub sign in`, e)
      return undefined
    })
  }
```

**File:** app/src/lib/stores/sign-in-store.ts (L332-359)
```typescript
  public async resolveOAuthRequest(action: IOAuthAction) {
    if (!this.state || this.state.kind !== SignInStep.Authentication) {
      return
    }

    if (!this.state.oauthState) {
      return
    }

    if (this.state.oauthState.state !== action.state) {
      log.warn(
        'requestAuthenticatedUser was not called with valid OAuth state. This is likely due to a browser reloading the callback URL. Contact GitHub Support if you believe this is an error'
      )
      return
    }

    const { endpoint } = this.state
    const token = await requestOAuthToken(endpoint, action.code)

    if (token) {
      const account = await fetchUser(endpoint, token)
      this.state.oauthState.onAuthCompleted(account)
    } else {
      this.state.oauthState.onAuthError(
        new Error('Failed retrieving authenticated user')
      )
    }
  }
```
