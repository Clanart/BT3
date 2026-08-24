## Analysis

The Sablier report's broken invariant: a fee-enforcement function trusts a coarse/default classification (`calculateMinFeeWei`) instead of the specific, verified classification for the actual counterparty (`calculateMinFeeWeiFor`), letting an attacker-influenced input take the weaker path.

The closest verified Desktop analog is in the Git credential-helper trampoline, where the "is this a GitHub host?" decision is made using an attacker-controlled signal (`WWW-Authenticate` header from a remote Git server) as a shortcut *before* falling back to the actual verified check (`isGitHubHost`), and that classification then drives which authentication flow is silently triggered.

### Title
Attacker-controlled `WWW-Authenticate` realm spoofs GitHub host classification and triggers Enterprise sign-in against an arbitrary remote - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind` classifies a Git remote as `'enterprise'` (i.e., a trusted GitHub Enterprise host) purely by inspecting the `WWW-Authenticate` header string a remote server returned, before doing any real verification such as the `isGitHubHost` network probe or checking against a known account. [1](#0-0) 

### Finding Description
`getEndpointKind` is used to decide whether credentials requested by Git (via the trampoline/askpass credential helper) should be handled as GitHub-managed (internal account token) or as "generic" (external) credentials: [2](#0-1) 

The function contains this explicit shortcut, commented as a "happy path":
```
for (const [k, v] of cred.entries()) {
    if (k.startsWith('wwwauth[')) {
      if (v.includes('realm="GitHub"')) {
        return 'enterprise'
      } else if (/realm="(GitLab|Gitea|Atlassian Bitbucket)"/.test(v)) {
        return 'generic'
      }
    }
}
```
This header comes directly from the HTTP response of the remote the user is cloning/fetching/pushing to — i.e., it's attacker-controlled if the attacker operates (or MITMs, or simply owns) the git server the victim points Desktop at. A malicious server can return `WWW-Authenticate: Basic realm="GitHub"` on the initial unauthenticated request, causing `getEndpointKind` to short-circuit to `'enterprise'` **without** ever invoking the actual verification path, `isGitHubHost(endpoint)`, which performs a genuine network check against `/desktop_internal` or similar API markers. [3](#0-2) 

Once classified as `'enterprise'`, `getCredential` sees there's no existing account for that endpoint and calls `ui.promptForGitHubSignIn(endpoint)`: [4](#0-3) 

`promptForGitHubSignIn` then begins a **GitHub Enterprise sign-in flow targeting the attacker's own origin**, since the hostname is not `github.com`: [5](#0-4) 

The enterprise sign-in flow accepts the attacker's URL as a real GHE endpoint (subject only to HTTPS-protocol syntactic validation, not identity/ownership validation), advances straight to the `Authentication` step, and will run OAuth/browser-based sign-in against that origin: [6](#0-5) [7](#0-6) 

The custom, more rigorous check (`isGitHubHost`, a real network probe) exists precisely to guard against this — but it is only reached in the `else` branch, after the spoofable header shortcut has already returned: [8](#0-7) 

### Impact Explanation
A malicious remote (any repository the user clones/adds a remote for, or any MITM'd/compromised proxy for an HTTP git operation) can force Desktop to present the user with a "Sign in to your GitHub Enterprise" dialog whose target endpoint is the attacker's own server, disguised as a routine credential prompt during a git operation. If the user proceeds, the OAuth flow (`requestOAuthToken`) sends the authorization `code`/exchanged token to the attacker-controlled endpoint via `requestOAuthToken(endpoint, action.code)`, and `fetchUser(endpoint, token)` is likewise issued against the attacker's host — i.e., the app's OAuth completion flow and any resulting account/token material is exchanged with a server the attacker controls, not GitHub. This can lead to credential/token exfiltration and creation of an "account" object populated from attacker-supplied data inside Desktop. [9](#0-8) 

### Likelihood Explanation
The trigger requires only that the victim performs a normal git operation (clone, fetch, push, pull) against a remote controlled by the attacker (a public malicious repo the user is asked to open, or a compromised/MITM'd proxy), and that Desktop attempts the credential-helper "get" flow, which happens automatically for HTTPS remotes lacking cached credentials. No local access, no prior malware, no unnatural steps beyond adding/cloning a repo (a normal Desktop workflow) are required — matching the "attacker controls a cloned/fetched repository or a git remote/proxy response" impact class.

### Recommendation
Remove or de-prioritize the `WWW-Authenticate` header heuristic as an authoritative signal for GitHub-host classification, or only trust it after corroborating with the actual `isGitHubHost` network check (or an existing known account) rather than allowing it to short-circuit before verification. At minimum, before initiating any sign-in flow (`promptForGitHubSignIn`) that will exchange OAuth codes/tokens with a remote origin, require independent verification (e.g., a successful `isGitHubHost` probe or explicit user confirmation naming the actual host) instead of accepting an unauthenticated response header as proof of identity.

### Proof of Concept
1. Attacker sets up an HTTPS git server (e.g., a proxy in front of a plain git repo) that responds to unauthenticated Git HTTP requests with `WWW-Authenticate: Basic realm="GitHub"`.
2. Victim clones or adds this URL as a remote in GitHub Desktop and performs a fetch/push.
3. Git invokes the trampoline credential helper's `get` command; `getEndpointKind` sees the spoofed header and returns `'enterprise'` without calling `isGitHubHost`.
4. Since no existing account matches this endpoint, `getCredential` calls `ui.promptForGitHubSignIn(attackerEndpoint)`, which calls `dispatcher.beginEnterpriseSignIn` and `setSignInEndpoint(attackerOrigin)`, presenting the user a "Sign in to your GitHub Enterprise" dialog for the attacker's host.
5. If the user proceeds with sign-in, `resolveOAuthRequest` exchanges the OAuth code and fetches the "user" against the attacker's endpoint, sending credential material to the attacker's server.

**Uncertainty note:** I was not able to trace the exact wire-level behavior of the `AuthenticationForm`/browser-based OAuth handshake (e.g., whether `beginBrowserBasedSignIn` is what actually fires for credential-helper-initiated sign-in, versus the in-app username/password `AuthenticationForm`) purely from the indexed snippets; a background Devin session with full repo access would be needed to confirm the precise data sent to the attacker endpoint during the `Authentication` step UI path.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-125)
```typescript
/** Implementation of the 'get' git credential helper command */
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

**File:** app/src/lib/api.ts (L2321-2334)
```typescript
/**
 * Get the API URL for an HTML URL. For example:
 *
 * http://github.mycompany.com -> https://github.mycompany.com/api/v3
 */
export function getEnterpriseAPIURL(endpoint: string): string {
  const { host } = new window.URL(endpoint)

  return isGHE(endpoint) ? `https://api.${host}/` : `https://${host}/api/v3`
}

export const getAPIEndpoint = (endpoint: string) =>
  isDotCom(endpoint) ? getDotComAPIEndpoint() : getEnterpriseAPIURL(endpoint)

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

**File:** app/src/ui/lib/enterprise-validate-url.ts (L14-45)
```typescript
export function validateURL(address: string): string {
  // ensure user has specified text and not just whitespace
  // we will interact with this server so we can be fairly
  // relaxed here about what we accept for the server name
  const trimmed = address.trim()
  if (trimmed.length === 0) {
    const error = new Error('Unknown address')
    error.name = InvalidURLErrorName
    throw error
  }

  let url = URL.parse(trimmed)
  if (!url.host) {
    // E.g., if they user entered 'ghe.io', let's assume they're using https.
    address = `https://${trimmed}`
    url = URL.parse(address)
  }

  if (!url.protocol) {
    const error = new Error('Invalid URL')
    error.name = InvalidURLErrorName
    throw error
  }

  if (url.protocol !== 'https:') {
    const error = new Error('Invalid protocol')
    error.name = InvalidProtocolErrorName
    throw error
  }

  return address
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

**File:** app/src/lib/stores/sign-in-store.ts (L394-459)
```typescript
  public async setEndpoint(url: string): Promise<void> {
    const currentState = this.state

    if (
      currentState?.kind !== SignInStep.EndpointEntry &&
      currentState?.kind !== SignInStep.ExistingAccountWarning
    ) {
      const stepText = currentState ? currentState.kind : 'null'
      return fatalError(
        `Sign in step '${stepText}' not compatible with endpoint entry`
      )
    }

    /**
     * If the user enters a github.com url in the GitHub Enterprise sign-in
     * flow we'll redirect them to the GitHub.com sign-in flow.
     */
    if (/^(?:https:\/\/)?(?:api\.)?github\.com($|\/)/.test(url)) {
      this.beginDotComSignIn(currentState.resultCallback)
      return
    }

    this.setState({ ...currentState, loading: true })

    let validUrl: string
    try {
      validUrl = validateURL(url)
    } catch (e) {
      let error = e
      if (e.name === InvalidURLErrorName) {
        error = new Error(
          `The GitHub Enterprise instance address doesn't appear to be a valid URL. We're expecting something like https://example.ghe.com.`
        )
      } else if (e.name === InvalidProtocolErrorName) {
        error = new Error(
          'Unsupported protocol. Only https is supported when authenticating with GitHub Enterprise instances.'
        )
      }

      this.setState({ ...currentState, loading: false, error })
      return
    }

    const endpoint = getEnterpriseAPIURL(validUrl)

    const existingAccount = this.accounts.find(x => x.endpoint === endpoint)

    if (existingAccount) {
      this.setState({
        kind: SignInStep.ExistingAccountWarning,
        endpoint,
        existingAccount,
        error: null,
        loading: false,
        resultCallback: currentState.resultCallback,
      })
    } else {
      this.setState({
        kind: SignInStep.Authentication,
        endpoint,
        error: null,
        loading: false,
        resultCallback: currentState.resultCallback,
      })
    }
  }
```
