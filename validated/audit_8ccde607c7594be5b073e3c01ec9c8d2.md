### Title
Spoofed `WWW-Authenticate` header from a malicious git remote lets an attacker force GitHub Enterprise OAuth sign‑in against their own server, exfiltrating the Desktop OAuth `client_secret` - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
Desktop's git credential-helper trampoline decides whether a remote host should be treated as a "GitHub" endpoint (`github.com` / GHE) by trusting an unauthenticated `WWW-Authenticate` header echoed back by the remote git server, *before* it ever performs the safe, verified check (`isGitHubHost()`, which actually probes the real API). A malicious/attacker-controlled git remote can self-declare `realm="GitHub"` to short-circuit that verification and force Desktop into the GitHub sign-in flow, scoped to the attacker's own endpoint. If the user proceeds with "Sign in using your browser," Desktop's OAuth flow will POST the bundled `client_id`/`client_secret` to the attacker's server instead of GitHub's, exfiltrating the app's OAuth secret and allowing the attacker to fabricate an `Account` object that Desktop then treats as a trusted, signed-in identity.

### Finding Description
In `getEndpointKind()`, endpoint classification is decided by iterating credential fields returned by git and trusting any `wwwauth[...]` value containing `realm="GitHub"` as proof the host is GitHub Enterprise, returning `'enterprise'` immediately: [1](#0-0) 

This header comes straight from the HTTP response of whatever remote the git operation targeted — fully attacker-controlled if the user has added/cloned from a malicious remote. Only *after* this early-return branch does the code fall back to the actually-verified check, `isGitHubHost(endpoint)`, which performs a real network probe against the claimed host: [2](#0-1) 

Because the spoofed-header branch runs first and returns unconditionally, the legitimate verification is never reached — the equivalent of the `PublicLock` bug where an attacker-suppliable value determines a trust decision before the intended, authorized initializer gets a chance to establish it.

Once classified as `'enterprise'` (or `'github.com'`) with no matching stored `Account`, `getCredential()` calls into the sign-in UI: [3](#0-2) 

which invokes `promptForGitHubSignIn(endpoint)`, binding the sign-in flow's endpoint to the attacker-controlled URL: [4](#0-3) 

If the user clicks "Sign in using your browser," `authenticateWithBrowser()` opens `getOAuthAuthorizationURL(endpoint, csrfToken)` in the system browser, pointed at the attacker's domain: [5](#0-4) [6](#0-5) 

When the attacker's server redirects back through the `x-github-client://oauth` deep link with a code, Desktop's OAuth-state check (which only validates the CSRF token, not the endpoint's authenticity) accepts it and calls `requestOAuthToken(endpoint, code)`, which POSTs the app's bundled `client_id`/`client_secret` straight to the attacker's own endpoint: [7](#0-6) [8](#0-7) 

The resulting "account" (identity + token) fetched from the attacker's fake API is then stored as a first-class Desktop `Account`, giving the attacker the ability to bind an arbitrary identity/token into Desktop's trusted accounts store.

### Impact Explanation
- **Credential exfiltration**: The application's OAuth `client_secret` (from `app-info.ts`, shared by all users of the bundled dev/prod client) is sent to an attacker-controlled server instead of GitHub's real OAuth endpoint.
- **Unauthorized account binding**: The attacker can return arbitrary user/token data which Desktop will treat as an authenticated GitHub account (`Account` object) trusted for future git operations against that endpoint.
- **Broken invariant**: Endpoint trust classification (`github.com` / GHE vs generic) is supposed to be gated by a verified API probe (`isGitHubHost`) but is actually decided by unauthenticated, attacker-suppliable header content that is checked first and returns early — mirroring the `PublicLock.initialize()` pattern where an unprivileged actor can front-run/force the trust-establishing step before the legitimate verification path executes.

### Likelihood Explanation
Requires only that the user add/clone/fetch from a git remote controlled by the attacker (a normal, expected Desktop workflow) and that the remote's HTTP 401 response include a crafted `WWW-Authenticate: realm="GitHub"` header — trivial for any attacker running their own git-over-HTTP server. No local access, malware, or leaked credentials are needed; it purely exploits attacker-controlled remote responses that Desktop already processes as part of the credential-helper flow.

### Recommendation
Remove (or de-prioritize below `isGitHubHost()`) the `wwwauth[]` "happy path" check in `getEndpointKind()`, or require it to be corroborated by the real API probe before classifying an endpoint as `enterprise`/`github.com`. At minimum, never allow an unverified/attacker-controlled classification result to trigger the OAuth `client_secret`-bearing sign-in flow.

### Proof of Concept
1. Stand up a git-over-HTTP(S) server that, on any authentication challenge, responds with `WWW-Authenticate: Basic realm="GitHub"` (no real GitHub API behind it).
2. In GitHub Desktop, clone or add this server as a remote and perform any operation requiring authentication (fetch/push).
3. `getEndpointKind()` matches the spoofed header at `trampoline-credential-helper.ts:159` and returns `'enterprise'` without ever calling `isGitHubHost()`.
4. Since no account is registered for the fake endpoint, Desktop shows the GitHub Enterprise sign-in popup bound to the attacker's URL (`trampoline-ui-helper.ts:87-93`).
5. If the user clicks "Sign in using your browser," Desktop opens `attacker-endpoint/login/oauth/authorize?client_id=...` and, on redirect, calls `requestOAuthToken` which POSTs `client_id`/`client_secret` to `attacker-endpoint/login/oauth/access_token`, leaking the OAuth secret and letting the attacker return a forged `Account`.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L153-166)
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L172-178)
```typescript
  // All GitHub hosts use HTTPS, so if the protocol is not HTTPS we can
  // assume that this is not a GitHub host.
  if (credentialUrl.protocol !== 'https:') {
    return 'generic'
  }

  return (await isGitHubHost(endpoint)) ? 'enterprise' : 'generic'
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
