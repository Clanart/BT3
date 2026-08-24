### Title
Stale-session OAuth callback allows a race that hijacks the sign-in flow's success/error callback across concurrent sign-in attempts - ([File: app/src/lib/stores/sign-in-store.ts])

### Summary
`SignInStore.resolveOAuthRequest` (invoked when the app's custom protocol handler receives an `x-github-desktop-auth://oauth?...` deep link parsed by `parseAppURL`) validates the incoming CSRF `state` *before* awaiting the network call to exchange the code for a token, but re-reads `this.state` *after* the await completes without re-validating that the CSRF state still corresponds to the in-flight session. If a second sign-in attempt (or a `reset()`) starts while the token exchange from the first attempt is still pending, the resolved token/account from the first (attacker- or race-influenced) OAuth callback gets delivered through the *second* session's `oauthState.onAuthCompleted`, mixing up which account is authenticated for which flow. [1](#0-0) 

### Finding Description
The custom-protocol OAuth callback is parsed into an `IOAuthAction { code, state }` by `parseAppURL` and is fully attacker-controllable content (an arbitrary link/deep link the user clicks, e.g. `x-github-desktop-auth://oauth?code=...&state=...`): [2](#0-1) 

`resolveOAuthRequest` checks that `action.state` matches the store's current `oauthState.state` (a CSRF token generated per sign-in attempt), but this check happens **before** the asynchronous `requestOAuthToken` / `fetchUser` calls: [1](#0-0) 

After the `await`, the code calls `this.state.oauthState.onAuthCompleted(account)` using whatever `this.state` happens to be *at that time* — it does not re-check that `this.state.oauthState.state` still equals the original `csrfToken`/`action.state`. Compare this to `authenticateWithBrowser`, which does perform this re-check after its own awaited promise resolves: [3](#0-2) 

If a user (or an attacker-influenced flow, e.g. clicking a second "Sign in" deep link, or the app auto-retrying `beginDotComSignIn`/`beginEnterpriseSignIn`) triggers a new sign-in attempt while an earlier `resolveOAuthRequest` await is still pending, `beginDotComSignIn`/`beginEnterpriseSignIn`/`reset()` will overwrite `this.state` with a new `oauthState` object (new CSRF token, new `resultCallback`). When the first, stale `resolveOAuthRequest` call's `await requestOAuthToken(...)` finally resolves, it reads the **new** `this.state.oauthState` and invokes `onAuthCompleted`/`onAuthError` on it — completing the *new* session with an account/token that was authenticated (or requested) under the *old* session's CSRF context. This is the same "reentrancy/misordering" class as the reported Solidity issue: state relied upon before an external call (`transfer`/`await`) is no longer valid by the time execution resumes, and the code fails to re-validate it, letting an interleaved call complete out of order and against stale invariants.

### Impact Explanation
This corrupts which account result is delivered to which sign-in flow/caller (`resultCallback`) or dialog. Concretely, `SignInStore` reuses `this.state` as global mutable session state shared across `Dispatcher._beginDotComSignIn` / `_beginEnterpriseSignIn` / `_setSignInEndpoint` call sites (`app/src/ui/dispatcher/dispatcher.ts`), so a race can cause the wrong account's authentication result (or an error) to be delivered to a UI flow that expects a different endpoint/account, or cause `emitAuthenticate` to fire for a stale/incorrect account association. In the worst case this misattributes a GitHub account/OAuth token to the wrong sign-in dialog session (unauthorized account binding), consistent with the "Valid Impact" criterion of unauthorized OAuth/account binding driven by an attacker-controlled deep link.

### Likelihood Explanation
Triggering requires the user to click two OAuth deep links (or trigger a second sign-in attempt) in quick succession while a prior OAuth exchange is in flight — no local/admin access, no leaked credentials, and no malware needed, only interaction with attacker-supplied links, matching the accepted attack surface ("a link or deep link the user clicks"). The window is bounded by the latency of `requestOAuthToken`/`fetchUser` network calls, and is more likely on slow networks; there is no code-level mitigation guarding against overlapping sign-in sessions in `resolveOAuthRequest` (unlike `authenticateWithBrowser`, which re-validates `csrfToken` post-await).

### Recommendation
In `resolveOAuthRequest`, capture the `oauthState`/CSRF token locally before the `await`, and re-check that `this.state?.kind === SignInStep.Authentication && this.state.oauthState?.state === action.state` (the same pattern already used in `authenticateWithBrowser`'s `.then`/`.catch`) immediately before calling `onAuthCompleted`/`onAuthError`, discarding the result if the session has since changed: [1](#0-0) 

### Proof of Concept
1. Start a GitHub.com sign-in (`beginDotComSignIn`), triggering `authenticateWithBrowser`, which opens the OAuth authorize URL with `csrfToken = A`.
2. Craft/click a deep link `x-github-desktop-auth://oauth?code=CODE1&state=A` — the app calls `resolveOAuthRequest`, which passes the state check and begins `await requestOAuthToken(endpoint, CODE1)`.
3. Before that request resolves, start a second sign-in flow (e.g., `beginEnterpriseSignIn` + `setEndpoint`), which calls `reset()`/`setState` and replaces `this.state.oauthState` with a new CSRF token `B` and a new `resultCallback`.
4. The pending `requestOAuthToken` for `CODE1` eventually resolves; `resolveOAuthRequest` reads the *current* `this.state.oauthState` (token `B`'s session) and calls `onAuthCompleted(account)` on it, completing session B with the account resolved for session A's OAuth code — despite the CSRF states never matching for session B.
5. Observe: `resultCallback` for the second (enterprise) sign-in flow fires with account data derived from the first (dotcom) OAuth code, and/or `emitAuthenticate` emits an authentication event for an unintended account/session.

### Citations

**File:** app/src/lib/stores/sign-in-store.ts (L304-329)
```typescript
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
