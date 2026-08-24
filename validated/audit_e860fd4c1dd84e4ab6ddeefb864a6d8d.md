## Finding

The report's core pattern — a privileged decision is made by trusting an **unverified, externally supplied signal** instead of the safe (but slower) verification path that exists specifically to prevent that trust — has a direct analog in GitHub Desktop's credential-helper / enterprise sign-in flow.

### Title
Attacker-controlled `WWW-Authenticate` header spoofs GitHub Enterprise trust classification, leading to OAuth client-secret exfiltration - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind()` classifies a git remote as a `github.com` / `enterprise` / `generic` host before Desktop decides whether to hand out stored account credentials or start an "enterprise" sign-in flow. The proper, safe way to determine "is this really a GitHub host" is the network probe `isGitHubHost()`, which makes an HTTPS request and only trusts a `x-github-request-id` response header from the actual server [1](#0-0) . However, before ever reaching that check, `getEndpointKind()` short-circuits on an attacker-controlled value: the `wwwauth[]` credential field, which is populated verbatim from the remote server's `WWW-Authenticate` HTTP header by Git itself and forwarded to the credential helper [2](#0-1) . If that header merely contains the substring `realm="GitHub"`, Desktop unconditionally classifies the host as `'enterprise'` — with zero cryptographic or network verification.

### Finding Description
`getEndpointKind` (`app/src/lib/trampoline/trampoline-credential-helper.ts:137-179`):
```
for (const [k, v] of cred.entries()) {
    if (k.startsWith('wwwauth[')) {
      if (v.includes('realm="GitHub"')) {
        return 'enterprise'
      } ...
``` [3](#0-2) 

This value comes straight from the remote server's HTTP response — fully attacker-controlled if the remote is attacker-operated (any self-hosted git server the victim adds as a remote or clones from). The comment in the code even documents this as a deliberate "happy path" shortcut to *avoid* calling the real verification (`isGitHubHost`) [4](#0-3) .

Once `getEndpointKind` returns `'enterprise'`, `getCredential()` checks whether an existing account matches the derived API endpoint; if not, it calls `ui.promptForGitHubSignIn(endpoint)` with `endpoint` still being the attacker's own host [5](#0-4) . That helper drives the normal Enterprise sign-in state machine: `dispatcher.beginEnterpriseSignIn(cb)` + `dispatcher.setSignInEndpoint(origin)` + a `PopupType.SignIn` dialog [6](#0-5) .

If the user proceeds with "sign in with browser" (a completely ordinary action when Desktop asks you to authenticate to a new remote), `authenticateWithBrowser()` opens `getOAuthAuthorizationURL(endpoint, csrfToken)` in the external browser, pointed at the attacker's own domain, and later — once a `code`/`state` pair comes back via the app's custom URL scheme — calls `requestOAuthToken(endpoint, action.code)` [7](#0-6) [8](#0-7) . `requestOAuthToken` POSTs the app's OAuth `client_id` **and `client_secret`** directly to `${endpoint}/login/oauth/access_token` — i.e., to the attacker's own server [9](#0-8) .

Because the attacker's server originated the `authorize` request (it saw `client_id` and `state` in the query string when the browser hit it), it can trivially redirect back to Desktop's registered protocol handler with any `code` value and the correctly-reflected `state`, satisfying the CSRF check in `resolveOAuthRequest` [10](#0-9) , and complete the loop.

### Impact Explanation
Desktop's app-bundled OAuth `client_secret` gets transmitted to an attacker-controlled server via a fully automated, standard-looking sign-in flow the victim did not consciously choose to run "against the attacker" — the attacker's remote silently redirected the trust decision. This is a form of credential/secret exfiltration entirely driven by an attacker-controlled git remote/proxy response, matching the requested impact class. It also illustrates a broader trust break: Desktop's own safeguard (`isGitHubHost`'s network verification) exists precisely to prevent an arbitrary host from being treated as GitHub Enterprise, but is bypassed by a heuristic that trusts attacker-supplied header text.

### Likelihood Explanation
Requires only that the victim adds/clones a repository from an attacker-operated HTTPS git remote and goes through Desktop's normal authentication prompt for that remote — no local access, no leaked credentials, and no unnatural steps; entering credentials/signing in when a new remote asks for it is expected Desktop UX. The main friction is that the victim must click "sign in with browser," but this is exactly what Desktop's UI already funnels users toward for Enterprise auth failures.

### Recommendation
Never let `getEndpointKind` (or any classification that grants "enterprise/GitHub" trust) rely on unauthenticated `WWW-Authenticate` header contents supplied by the remote itself. That heuristic should, at most, be used to *prioritize* calling `isGitHubHost()`, not to bypass it. Additionally, before starting an Enterprise OAuth sign-in flow (`beginEnterpriseSignIn`/`setSignInEndpoint`) or issuing `requestOAuthToken`, Desktop should require the endpoint to have passed the network-based `isGitHubHost()` check (or be explicitly typed in by the user through the manual Enterprise sign-in `EndpointEntry` step, which already calls `validateURL`), rather than deriving it from an unauthenticated header echoed by the remote.

### Proof of Concept
1. Attacker stands up an HTTPS git server at `https://attacker.example`, serving a repo the victim is enticed to clone/add as a remote.
2. Configure the server to respond to Git's authentication probe with `WWW-Authenticate: Basic realm="GitHub"`.
3. Victim, in GitHub Desktop, fetches/pushes to this remote; Git forwards the header to Desktop's credential helper as `wwwauth[0]=Basic realm="GitHub"`.
4. `getEndpointKind` returns `'enterprise'` without ever calling `isGitHubHost()` (`trampoline-credential-helper.ts:157-165`).
5. No matching enterprise account exists, so `promptForGitHubSignIn('https://attacker.example')` opens Desktop's normal Enterprise sign-in dialog.
6. Victim clicks "Sign in with your browser." Desktop opens `https://attacker.example/login/oauth/authorize?client_id=<real client id>&state=<csrf>` in the OS browser (`sign-in-store.ts:284-303`).
7. Attacker's server immediately redirects to Desktop's registered URL scheme with the reflected `state` and an arbitrary `code`.
8. Desktop's `resolveOAuthRequest` accepts the matching `state` and calls `requestOAuthToken('https://attacker.example', code)`, POSTing `client_id`/`client_secret`/`code` to the attacker's server (`sign-in-store.ts:332-359`, `api.ts:2370-2395`).
9. Attacker now has GitHub Desktop's OAuth `client_secret`.

I was not able to independently verify the exact custom-URL-scheme handling code (`resolveOAuthRequest`'s caller/`parse-app-url.ts`) within the available index depth; if further confirmation of the deep-link redirect handling is needed, a full Devin session with complete file access would be required to trace that path end-to-end.

### Citations

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

**File:** app/src/lib/api.ts (L2429-2490)
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
```

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L153-165)
```typescript
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

**File:** app/src/lib/stores/sign-in-store.ts (L284-303)
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
