## Title
OAuth authorization code interception via URI-scheme hijacking of `x-github-client://` / `github-mac` / `github-windows` — ([File: app/src/main-process/main.ts])

### Summary
GitHub Desktop uses OS-level custom URI schemes (`x-github-client`, `x-github-desktop-auth`/`x-github-desktop-dev-auth`, and the legacy `github-mac`/`github-windows`) as its OAuth redirect target and as the general "deep link" mechanism for the app. [1](#0-0)  Just like the iOS `uniswap://` finding, these custom schemes are not exclusively owned by the Desktop app at the OS level: on Windows and macOS, any other locally installed application that registers the same scheme can be invoked instead of (or race with) GitHub Desktop when the OAuth provider redirects back with `?code=...&state=...`. Because Desktop's OAuth flow uses a public, non-confidential client (client secret is compiled into every build) and does not use PKCE, possession of the intercepted authorization `code` is sufficient to redeem an access token for the signed-in GitHub account.

### Finding Description
Desktop registers itself as the handler for several custom protocols at startup: [1](#0-0) [2](#0-1) 

The OAuth sign-in flow opens the authorization URL in the system browser and waits for the browser to redirect back to `x-github-client://oauth?code=...&state=...`: [3](#0-2) 

`parseAppURL` extracts `code` and `state` from any URL whose hostname is `oauth`, without validating the scheme itself: [4](#0-3) 

`resolveOAuthRequest` checks that `state` matches the CSRF token generated for the current sign-in session, then exchanges the `code` for a token: [5](#0-4) 

The `state` check only protects against *cross-session* replay/injection (a different sign-in attempt's code being accepted). It does nothing to stop an attacker who intercepts the *same* redirect that was meant for the legitimate Desktop instance — the attacker simply forwards the same `code`/`state` pair it captured. Once a malicious app registers as a handler for `x-github-client://` (or the legacy `github-mac`/`github-windows` schemes, both of which are always registered when built for that platform), the OS may hand the redirect to either app; on Windows in particular, `app.setAsDefaultProtocolClient` calls are last-write-wins and don't grant exclusive ownership of the scheme.

Crucially, Desktop's OAuth client is a public/native client: the client ID and client secret are baked into every distributed build via `app-info.ts`, and there's no PKCE `code_verifier`/`code_challenge` binding tying redemption of the `code` to the same process that initiated the request: [6](#0-5) 

Since the secret is public (embedded in every install) and no PKCE verifier is required, a rogue application that intercepts the redirect URL can independently call the same token endpoint with the stolen `code` and the well-known client_id/secret and obtain a valid GitHub access token for the victim's account — exactly analogous to the iOS report's "rogue app receives the credential intended for the real app" scenario, but here the "credential" is a live GitHub OAuth authorization code.

### Impact Explanation
A locally co-installed malicious application that registers the same custom URL scheme can silently capture the OAuth authorization code intended for GitHub Desktop and redeem it for a GitHub access token, resulting in full account takeover of the user's GitHub identity (repository read/write, ability to push malicious commits, manage private repos, etc.), without the user ever suspecting anything beyond "the browser redirected me back to Desktop."

### Likelihood Explanation
This requires the victim to already have a competing app installed that claims the same protocol scheme (e.g., another Electron/native app that also happens to register `x-github-client://`, `github-mac://`, or `github-windows://`, or a malicious app deliberately published to register one of these well-documented, publicly known scheme names — they appear directly in Desktop's own source and docs). This is not "malware already controlling the host" in the sense of arbitrary local compromise; it is the standard "unprivileged app installed side-by-side" threat model that the OAuth-for-native-apps guidance (RFC 8252) and the Trail of Bits report both treat as in-scope, since installing an app is a normal, low-privilege user action and does not require admin rights or an already-compromised machine.

### Recommendation
- **Short term:** Bind the authorization code to the specific sign-in attempt using PKCE (`code_verifier`/`code_challenge`), so a captured `code` alone is useless to any process other than the one holding the original verifier generated in `authenticateWithBrowser`. Treat `x-github-client`/`github-mac`/`github-windows` schemes as untrusted transports and never assume exclusivity.
- **Long term:** Migrate the OAuth redirect to a platform-exclusive mechanism — macOS/iOS "Universal Links" equivalent, or on Windows, an HTTPS loopback redirect (`http://127.0.0.1:<port>/callback`) as recommended by RFC 8252 for native app OAuth flows, which cannot be squatted by another installed application the way a custom URI scheme can.

### Proof of Concept
1. Install a second application on Windows/macOS that also calls `app.setAsDefaultProtocolClient('x-github-client')` (or `github-mac`/`github-windows`).
2. In GitHub Desktop, trigger sign-in (`authenticateWithBrowser` in `app/src/lib/stores/sign-in-store.ts:260-303`), which opens the system browser to the OAuth authorize URL.
3. Complete authentication in the browser; the browser redirects to `x-github-client://oauth?code=...&state=...`.
4. Depending on OS protocol-handler resolution, the malicious app (registered for the same scheme) receives the callback instead of, or in a race with, Desktop.
5. The malicious app parses `code` from the URL and calls GitHub's OAuth token endpoint directly using the publicly known `__OAUTH_CLIENT_ID__`/`__OAUTH_SECRET__` embedded in every Desktop build (`app/app-info.ts:5-21`), obtaining a valid access token for the victim's GitHub account — with no PKCE verifier required to complete the exchange, since `resolveOAuthRequest` (`app/src/lib/stores/sign-in-store.ts:332-359`) only validates `state`, not proof-of-possession of the original request.

### Citations

**File:** app/src/main-process/main.ts (L105-116)
```typescript
const possibleProtocols = new Set(['x-github-client'])
if (__DEV_SECRETS__) {
  possibleProtocols.add('x-github-desktop-dev-auth')
} else {
  possibleProtocols.add('x-github-desktop-auth')
}
// Also support Desktop Classic's protocols.
if (__DARWIN__) {
  possibleProtocols.add('github-mac')
} else if (__WIN32__) {
  possibleProtocols.add('github-windows')
}
```

**File:** app/src/main-process/main.ts (L326-333)
```typescript
app.on('ready', () => {
  if (isDuplicateInstance || handlingSquirrelEvent) {
    return
  }

  readyTime = now() - launchTime

  possibleProtocols.forEach(protocol => setAsDefaultProtocolClient(protocol))
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

**File:** app/src/lib/parse-app-url.ts (L66-85)
```typescript
export function parseAppURL(url: string): URLActionType {
  const parsedURL = URL.parse(url, true)
  const hostname = parsedURL.hostname
  const unknown: IUnknownAction = { name: 'unknown', url }
  if (!hostname) {
    return unknown
  }

  const query = parsedURL.query

  const actionName = hostname.toLowerCase()
  if (actionName === 'oauth') {
    const code = getQueryStringValue(query, 'code')
    const state = getQueryStringValue(query, 'state')
    if (code != null && state != null) {
      return { name: 'oauth', code, state }
    } else {
      return unknown
    }
  }
```

**File:** app/app-info.ts (L5-21)
```typescript
const devClientId = '3a723b10ac5575cc5bb9'
const devClientSecret = '22c34d87789a365981ed921352a7b9a8c3f69d54'

const channel = getChannel()

const s = JSON.stringify

const optionalStringReplacement = (value: string | undefined) =>
  value === undefined || value.length === 0 ? 'undefined' : s(value)

export function getReplacements() {
  const isDevBuild = channel === 'development'

  return {
    __OAUTH_CLIENT_ID__: s(process.env.DESKTOP_OAUTH_CLIENT_ID || devClientId),
    __OAUTH_SECRET__: s(
      process.env.DESKTOP_OAUTH_CLIENT_SECRET || devClientSecret
```
