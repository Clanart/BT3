### Title
Attacker-controlled `WWW-Authenticate` realm header can trick the credential-helper into treating a hostile host as "GitHub Enterprise", leading to exfiltration of Desktop's OAuth client secret - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
Git's credential helper protocol forwards any `WWW-Authenticate` response header from the remote server as a `wwwauth[]` field to the credential helper. `getEndpointKind()` trusts this attacker-controlled header content to classify an arbitrary, unknown host as a GitHub Enterprise (`'enterprise'`) endpoint whenever it contains `realm="GitHub"`, without any independent verification (that fallback only happens later, via `isGitHubHost()`, when no wwwauth hint is present). This mirrors the reported bug class: a trust decision/binding (here, "is this endpoint GitHub Enterprise, and thus eligible for our OAuth sign-in flow") is made without validating the actual grantee/endpoint, and it flows into a privileged operation (issuing the real OAuth `client_id`/`client_secret` against that endpoint) that was never re-validated for the new, attacker-supplied "endpoint".

### Finding Description
When Desktop performs any git network operation (`fetch`, `clone`, `push`) the trampoline credential helper is invoked, and its `getCredentialUrl(cred)`/`getEndpointKind()` logic decides how to react to an authentication challenge: [1](#0-0) 

If the responding server (fully attacker-controlled - a malicious/compromised git host or a MITM proxy) sends `WWW-Authenticate: Basic realm="GitHub"`, `getEndpointKind` returns `'enterprise'` for a host that is neither `github.com`, `*.ghe.com`, nor a known account - no network confirmation via `isGitHubHost()` is performed in this branch.

Because `endpointKind !== 'generic'` and no existing account matches this brand-new host, Desktop calls `ui.promptForGitHubSignIn(endpoint)`: [2](#0-1) 

`promptForGitHubSignIn` feeds the attacker's origin straight into the normal Enterprise sign-in flow: [3](#0-2) 

`setEndpoint()` only checks protocol via `validateURL` (HTTPS required, nothing else): [4](#0-3) [5](#0-4) 

If the user clicks "Sign in using your browser" (a normal, expected action for this dialog), `authenticateWithBrowser()` opens the OS browser at the attacker's own host with Desktop's real, hard-coded OAuth `client_id`: [6](#0-5) [7](#0-6) 

Since the "GitHub Enterprise" endpoint is entirely attacker-owned, the attacker's server can immediately respond (no real GitHub OAuth consent screen needed) by invoking Desktop's OAuth callback protocol handler with an attacker-chosen `code` and the correct `state`. `resolveOAuthRequest` then exchanges that code for a token by POSTing Desktop's real `client_id`/`client_secret` to the same attacker-controlled host: [8](#0-7) [9](#0-8) 

`requestOAuthToken` builds its request URL from `getHTMLURL(endpoint)`, which for a non-dotcom, non-`.ghe.com` endpoint is just `${protocol}//${hostname}` of the attacker's host - i.e., the app's OAuth `client_secret` (`__OAUTH_SECRET__`, a single, global secret shared by every Desktop installation) is sent directly to the attacker: [10](#0-9) [11](#0-10) 

The broken invariant: `getEndpointKind`'s classification of a host as a legitimate GitHub Enterprise endpoint (derived from attacker-supplied `WWW-Authenticate` content) is trusted for the rest of the sign-in/OAuth-token-exchange pipeline without ever confirming the host is actually a GitHub host. No existing guard re-validates the endpoint before the client secret is transmitted - `validateURL` only checks the URL scheme, and `isGitHubHost()` (the actual verification function) is only used as a *fallback* when the wwwauth heuristic doesn't already short-circuit the decision.

### Impact Explanation
Successful exploitation leaks GitHub Desktop's single, application-wide OAuth `client_secret` to an attacker. Because this secret is shared across all installations of Desktop (it's compiled in at build time, not per-user), an attacker who obtains it can impersonate the GitHub Desktop OAuth application in phishing flows against other users, or use it in conjunction with intercepted authorization codes to obtain access tokens for arbitrary victims. This is a credential/secret exfiltration issue and enables unauthorized OAuth flows, both of which are explicitly listed as valid impact categories.

### Likelihood Explanation
The trigger is entirely attacker-controlled: any git remote/proxy the user fetches, clones, or pushes to (e.g., a malicious/compromised HTTP git server, or a network MITM position) can respond with a crafted `WWW-Authenticate: Basic realm="GitHub"` header. No admin rights, local access, or pre-existing malware are required. Some interaction is needed (the user must click "Sign in using your browser" on the resulting sign-in prompt), but that dialog appears as a normal consequence of doing a git operation Desktop already initiated, so this is not an "unnatural" step for the user.

### Recommendation
- In `getEndpointKind`, never classify an arbitrary host as `'enterprise'` (or any non-`generic` kind) solely from an attacker-suppliable `WWW-Authenticate` realm string; require the same `isGitHubHost()` (or stronger) verification that gates the fallback path.
- Before opening a browser-based OAuth flow or exchanging an authorization code for a token, independently re-verify that the target endpoint is a genuine, previously-known-good GitHub/GHE host (e.g., via the `/meta` `x-github-request-id` check that `isGitHubHost` already performs), rather than trusting the value that triggered the sign-in prompt.
- Consider not auto-populating/prefilling the Enterprise sign-in endpoint from credential-helper-derived data at all; require explicit manual entry validated the same way as normal "Add Enterprise Account" flows.

### Proof of Concept
1. Set up a git HTTP server (e.g. `evil.example.com`) that, for any authenticated request, responds with `401` and header `WWW-Authenticate: Basic realm="GitHub"`.
2. Have the victim add this URL as a remote (or clone from it) in GitHub Desktop and perform a fetch/push.
3. Desktop's credential helper (`getCredentialUrl`/`getEndpointKind` in `app/src/lib/trampoline/trampoline-credential-helper.ts:137-179`) sees the `wwwauth[...]` entry containing `realm="GitHub"` and classifies `evil.example.com` as `'enterprise'`.
4. Since no account exists for `evil.example.com`, Desktop calls `promptForGitHubSignIn` (`app/src/lib/trampoline/trampoline-ui-helper.ts:80-104`), which sets the sign-in endpoint to `https://evil.example.com` via `setSignInEndpoint`, passing only the HTTPS-scheme check in `validateURL` (`app/src/ui/lib/enterprise-validate-url.ts`).
5. The victim clicks "Sign in using your browser" on the resulting dialog; Desktop opens `https://evil.example.com/login/oauth/authorize?client_id=<REAL_CLIENT_ID>&scope=repo+user+workflow&state=<csrf>` (`app/src/lib/stores/sign-in-store.ts:284-303`, `app/src/lib/api.ts:2357-2368`).
6. The attacker's server, having full control of that domain, immediately invokes Desktop's registered OAuth callback protocol with the correct `state` and any `code` it chooses.
7. `resolveOAuthRequest` (`app/src/lib/stores/sign-in-store.ts:332-359`) calls `requestOAuthToken('https://evil.example.com/api/v3', code)`, which POSTs `{client_id, client_secret, code}` to `https://evil.example.com/login/oauth/access_token` (`app/src/lib/api.ts:2370-2395`), handing the real Desktop OAuth `client_secret` to the attacker. [1](#0-0) [3](#0-2) [4](#0-3) [8](#0-7) [9](#0-8)

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L107-125)
```typescript
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

**File:** app/src/lib/stores/sign-in-store.ts (L284-330)
```typescript
    const csrfToken = crypto.randomUUID()

    new Promise<Account>((resolve, reject) => {
      const { endpoint, resultCallback } = currentState
      log.info('[SignInStore] initializing OAuth flow')
      this.setState({
        kind: SignInStep.Authentication,
        endpoint,
        resultCallback,
        error: null,
        loading: true,
        oauthState: {
          state: csrfToken,
          endpoint,
          onAuthCompleted: resolve,
          onAuthError: reject,
        },
      })
      shell.openExternal(getOAuthAuthorizationURL(endpoint, csrfToken))
    })
      .then(account => {
        if (!this.state || this.state.kind !== SignInStep.Authentication) {
          // Looks like the sign in flow has been aborted
          log.warn('[SignInStore] account resolved but session has changed')
          return
        }

        log.info('[SignInStore] account resolved')
        this.emitAuthenticate(account)
        this.setState({
          kind: SignInStep.Success,
          resultCallback: this.state.resultCallback,
        })
      })
      .catch(e => {
        // Make sure we're still in the same sign in session
        if (
          this.state?.kind === SignInStep.Authentication &&
          this.state.oauthState?.state === csrfToken
        ) {
          log.info('[SignInStore] error with OAuth flow', e)
          this.setState({ ...this.state, error: e, loading: false })
        } else {
          log.info(`[SignInStore] OAuth error but session has changed: ${e}`)
        }
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

**File:** app/src/lib/stores/sign-in-store.ts (L394-437)
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

**File:** app/src/lib/api.ts (L132-139)
```typescript
const ClientID = process.env.TEST_ENV ? '' : __OAUTH_CLIENT_ID__
const ClientSecret = process.env.TEST_ENV ? '' : __OAUTH_SECRET__

if (!ClientID || !ClientID.length || !ClientSecret || !ClientSecret.length) {
  log.warn(
    `DESKTOP_OAUTH_CLIENT_ID and/or DESKTOP_OAUTH_CLIENT_SECRET is undefined. You won't be able to authenticate new users.`
  )
}
```

**File:** app/src/lib/api.ts (L2288-2319)
```typescript
export function getHTMLURL(endpoint: string): string {
  if (envHTMLURL !== undefined) {
    return envHTMLURL
  }

  // In the case of GitHub.com, the HTML site lives on the parent domain.
  //  E.g., https://api.github.com -> https://github.com
  //
  // Whereas with Enterprise, it lives on the same domain but without the
  // API path:
  //  E.g., https://github.mycompany.com/api/v3 -> https://github.mycompany.com
  //
  // We need to normalize them.
  if (endpoint === getDotComAPIEndpoint() && !envEndpoint) {
    return 'https://github.com'
  } else {
    if (isGHE(endpoint)) {
      const url = new window.URL(endpoint)

      url.pathname = '/'

      if (url.hostname.startsWith('api.')) {
        url.hostname = url.hostname.replace(/^api\./, '')
      }

      return url.toString()
    }

    const parsed = URL.parse(endpoint)
    return `${parsed.protocol}//${parsed.hostname}`
  }
}
```

**File:** app/src/lib/api.ts (L2357-2368)
```typescript
export function getOAuthAuthorizationURL(
  endpoint: string,
  state: string
): string {
  const urlBase = getHTMLURL(endpoint)
  const scope = encodeURIComponent(oauthScopes.join(' '))

  return new window.URL(
    `/login/oauth/authorize?client_id=${ClientID}&scope=${scope}&state=${state}`,
    urlBase
  ).toString()
}
```

**File:** app/src/lib/api.ts (L2370-2395)
```typescript
export async function requestOAuthToken(
  endpoint: string,
  code: string
): Promise<string | null> {
  try {
    const urlBase = getHTMLURL(endpoint)
    const response = await request(
      urlBase,
      null,
      'POST',
      'login/oauth/access_token',
      {
        client_id: ClientID,
        client_secret: ClientSecret,
        code: code,
      }
    )
    tryUpdateEndpointVersionFromResponse(endpoint, response)

    const result = await parsedResponse<IAPIAccessToken>(response)
    return result.access_token
  } catch (e) {
    log.warn(`requestOAuthToken: failed with endpoint ${endpoint}`, e)
    return null
  }
}
```
