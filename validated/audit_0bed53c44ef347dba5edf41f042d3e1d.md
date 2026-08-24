This confirms `code` and `state` in `IOAuthAction` come directly from the deep-link URL query string (`x-github-client://oauth?code=...&state=...`) — attacker-controlled if the user is tricked into clicking a crafted link. [1](#0-0) 

### Title
Stale OAuth callback can complete a *different*, newer sign-in session with a mismatched account - (File: app/src/lib/stores/sign-in-store.ts)

### Summary
`SignInStore.resolveOAuthRequest` validates the OAuth `state` (CSRF token) **before** issuing the network calls, but never re-validates it **after** the `await`s resolve. If `reset()` (or any state transition) happens while `requestOAuthToken`/`fetchUser` are in flight, the delayed response ends up being delivered into whatever `this.state.oauthState` happens to be *at that later time* — which can belong to a brand-new sign-in session.

### Finding Description
`resolveOAuthRequest` captures the CSRF check only once: [2](#0-1) 

It checks `this.state.oauthState.state !== action.state` up front, then does `await requestOAuthToken(...)` and `await fetchUser(...)`, and only afterwards dereferences `this.state.oauthState.onAuthCompleted(account)` again — without re-checking that `this.state.oauthState.state` still equals `action.state`.

Meanwhile, `reset()` does not cancel the in-flight `requestOAuthToken`/`fetchUser` calls — it only rejects the *old* `oauthState.onAuthError` and nulls out `this.state`: [3](#0-2) 

If the user (or a subsequent flow) calls `beginDotComSignIn`/`authenticateWithBrowser` again before the first network round-trip resolves, a brand-new `oauthState` (new `csrfToken`, new `resolve`/`reject`) is installed on `this.state`: [4](#0-3) 

When the stale response for the *first* flow finally arrives, line 353 fires `this.state.oauthState.onAuthCompleted(account)` against the *second* session's `oauthState`, resolving the second session's promise with the account object obtained from the first (possibly attacker-influenced) exchange. The `.then()` handler for that second promise only checks `this.state.kind !== SignInStep.Authentication`, not that the resolved account actually originated from its own `csrfToken`: [5](#0-4) 

Notably, the `.catch()` branch of the same function *does* perform this extra `oauthState.state === csrfToken` re-check before surfacing an error, but the success (`.then()`) path and `resolveOAuthRequest` do not apply the equivalent check before calling `onAuthCompleted`. This asymmetry is the root cause.

`action.code`/`action.state` are parsed straight from the incoming deep-link URL with no additional authentication: [1](#0-0) 

### Impact Explanation
Practically, exploiting this requires convincing timing: (1) a first OAuth attempt is initiated and its callback deep link fires `resolveOAuthRequest`, (2) before the two awaited network calls (`requestOAuthToken` → `fetchUser`, both to the endpoint's own API) return, the sign-in flow is reset and a second flow started, and (3) the first response then completes and lands in the second session. Since `requestOAuthToken`/`fetchUser` target the legitimate GitHub.com/Enterprise API (not attacker-controlled infrastructure for dotcom sign-in), the “attacker-controlled API response” framing in the question mostly applies to a malicious/compromised GitHub Enterprise endpoint that can deliberately delay its response to win this race, or to a user simply cancelling and retrying sign-in quickly. If achieved, the impact is an account-binding mix-up: the second sign-in session (which the user believes reflects their new choice/account) gets completed with the account resolved from the first, stale exchange — a form of unintended/unauthorized account binding.

### Likelihood Explanation
Low-to-moderate. It requires a race between a slow OAuth token/user exchange and a `reset()` + new `beginDotComSignIn`/`authenticateWithBrowser` call happening in that narrow window, which in the dotcom case depends on the user cancelling and retrying quickly, or in the GHE case on a malicious/rogue Enterprise server intentionally delaying its response to align with a user-initiated reset. There's no attacker-triggerable cancellation from outside the app (deep links only supply `code`/`state`, not a "cancel" action), so the reset step depends on normal user interaction with the sign-in UI (e.g., clicking "Cancel" and starting again), which somewhat limits real-world exploitability compared to a fully remote attack.

### Recommendation
In `resolveOAuthRequest`, capture the target `oauthState` reference (and/or the expected `csrfToken`) before the `await`s, and re-verify `this.state?.kind === SignInStep.Authentication && this.state.oauthState === capturedOauthState` (or `this.state.oauthState?.state === action.state`) immediately before calling `onAuthCompleted`/`onAuthError`. Apply the same re-validation in the `.then()` success handler of `authenticateWithBrowser` that currently exists only in the `.catch()` handler, so a resolved account is only ever applied to the session whose CSRF token it corresponds to. Consider also using an `AbortController` to cancel in-flight `requestOAuthToken`/`fetchUser` calls when `reset()` is invoked.

### Proof of Concept
1. Call `signInStore.beginDotComSignIn()` then `authenticateWithBrowser()` — this sets `oauthState` with `csrfToken1`.
2. Simulate the deep-link callback: call `signInStore.resolveOAuthRequest({name:'oauth', code:'code1', state: csrfToken1})`, but stub `requestOAuthToken`/`fetchUser` to return a pending, unresolved promise (or delay it).
3. Before those promises resolve, call `signInStore.reset()`, then `signInStore.beginDotComSignIn()` + `authenticateWithBrowser()` again — this installs a new `oauthState` with `csrfToken2`.
4. Resolve the stubbed `requestOAuthToken`/`fetchUser` promises from step 2 with a test `account`.
5. Observe that `emitAuthenticate` fires and the *second* session transitions to `SignInStep.Success` using the account resolved from the *first* (stale) exchange, and that the second session's own `resultCallback`/`onDidAuthenticate` receives an account never validated against `csrfToken2`.

### Citations

**File:** app/src/lib/parse-app-url.ts (L77-85)
```typescript
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

**File:** app/src/lib/stores/sign-in-store.ts (L212-220)
```typescript
  public reset() {
    const currentState = this.state
    this.state?.resultCallback({ kind: 'cancelled' })
    this.setState(null)

    if (currentState?.kind === SignInStep.Authentication) {
      currentState.oauthState?.onAuthError(new Error('cancelled'))
    }
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

**File:** app/src/lib/stores/sign-in-store.ts (L304-317)
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
