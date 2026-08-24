### Title
TOCTOU race in `SignInStore.resolveOAuthRequest` lets a slow OAuth callback complete a *different* sign-in session — (File: `app/src/lib/stores/sign-in-store.ts`)

### Summary
`resolveOAuthRequest` validates the OAuth `state` (CSRF) token against `this.state.oauthState` **before** the asynchronous token-exchange/user-fetch calls, but resolves the promise via `this.state.oauthState.onAuthCompleted(account)` **after** those awaits, re-reading `this.state` live instead of using the object it validated. If the active sign-in session changes in between (via `reset()` + a new `beginDotComSignIn`/`beginEnterpriseSignIn`/`authenticateWithBrowser` call), the account fetched for the original (now-abandoned) session is delivered into the *new* session's promise.

### Finding Description
`authenticateWithBrowser` (app/src/lib/stores/sign-in-store.ts:260-330) creates a per-session `oauthState` object containing a freshly generated `crypto.randomUUID()` CSRF token and a `resolve`/`reject` pair (`onAuthCompleted`/`onAuthError`) tied to *that* session's `Promise<Account>`.

`resolveOAuthRequest` (app/src/lib/stores/sign-in-store.ts:332-359), which is invoked from the deep-link/protocol handler with attacker/user-controlled `code` and `state` query values parsed in `parseAppURL` (app/src/lib/parse-app-url.ts:77-85), performs the CSRF check only once, at the top: [1](#0-0) 

The check `this.state.oauthState.state !== action.state` happens before `await requestOAuthToken(...)` and `await fetchUser(...)`. Both of these are network calls with non-trivial, attacker/network-influenceable latency. After they resolve, the code does `this.state.oauthState.onAuthCompleted(account)` — it does **not** capture the validated `oauthState` object in a local variable before the awaits, and it does **not** re-validate that `this.state.oauthState.state` is still equal to `action.state` after the awaits.

Meanwhile, `reset()` (app/src/lib/stores/sign-in-store.ts:212-220) is called whenever a new sign-in flow begins (`beginDotComSignIn`/`beginEnterpriseSignIn`, lines 226-231 and 366-371) while a prior session is in progress. `reset()` rejects the *old* session's promise via `onAuthError`, then `setState(null)`, and the subsequent `begin...SignIn`/`authenticateWithBrowser` call installs a brand-new `oauthState` object (new `state` token, new `resolve`/`reject` pair) on `this.state`.

Because `resolveOAuthRequest` re-reads `this.state.oauthState` fresh after its awaits, if session A's callback is being processed (passed the CSRF check while A was still active) and, before the network round trip completes, the user resets and starts session B, then `this.state.oauthState.onAuthCompleted` at completion time is actually **B's** `resolve` function. Session A's account (fetched using A's `code`/`endpoint`) is delivered into B's promise. B's `.then()` handler (lines 304-317) only checks `this.state.kind === SignInStep.Authentication` — it does not verify the account belongs to the session that was validated — so it calls `emitAuthenticate(account)` and transitions to `Success` using B's `resultCallback`, effectively binding session B's sign-in flow (and its consumer/result callback) to session A's account.

### Impact Explanation
This is an unauthorized OAuth/account-binding defect: the app can complete a sign-in flow with an account that was never validated against the session's own CSRF `state`, purely due to a check-then-act race in `resolveOAuthRequest`. In a scenario where two different accounts are being signed into in quick succession (e.g., a user backs out of one sign-in and starts another, then a stale browser tab/deep link for the first is later triggered), the app can silently authenticate/attach the wrong account to the second flow's caller, without any error surfaced.

### Likelihood Explanation
Triggering it requires: (1) a first sign-in session A's OAuth callback to be in-flight (delayed by real network latency to GitHub's token endpoint), (2) the user (or automation) to reset/start a second sign-in session B before A's exchange completes, and (3) B's session to still be in the `Authentication` step when A's exchange resolves. This is a plausible, realistic sequence (e.g., a user opens the browser for sign-in, cancels, tries a different account, and the original browser tab is later interacted with) but depends on timing rather than a directly forgeable/repeatable attacker payload — the attacker cannot forge the `state` token (it's a `crypto.randomUUID()`), and reliably winning the race generally requires influencing timing (e.g., network delay) rather than a single deterministic remote trigger.

### Recommendation
In `resolveOAuthRequest`, capture the validated `oauthState` object (or at minimum the `state` token) into a local variable before the `await` calls, and re-validate `this.state?.oauthState === capturedOauthState` (or `this.state.oauthState?.state === capturedState`) immediately before calling `onAuthCompleted`/`onAuthError`. This ensures the resolved account is only ever delivered to the session that was actually validated, mirroring the defensive check already present in `authenticateWithBrowser`'s `.catch()` handler (app/src/lib/stores/sign-in-store.ts:318-329) which does perform this re-check for the error path but is missing for the resolve path in `resolveOAuthRequest` itself.

### Proof of Concept
1. Start sign-in A (GitHub.com), triggering `authenticateWithBrowser`; session A gets `oauthState.state = csrfA`.
2. GitHub redirects to the deep link `x-github-desktop-auth://oauth?code=codeA&state=csrfA`; this reaches `resolveOAuthRequest`, passes the CSRF check (`this.state.oauthState.state === action.state`), and begins `await requestOAuthToken(endpointA, codeA)` (simulate/observe real network delay).
3. Before that await resolves, call `reset()` (e.g., cancel the sign-in dialog) and immediately start sign-in B (a different GitHub Enterprise endpoint), which sets a new `oauthState.state = csrfB` on `this.state`.
4. Let step 2's `requestOAuthToken`/`fetchUser` complete; observe that `this.state.oauthState.onAuthCompleted(account)` fires — since `this.state` is now B's state, this resolves **B's** `authenticateWithBrowser()` promise with **A's** account, `emitAuthenticate` fires with A's account, and B's flow transitions to `Success` using B's `resultCallback`. [2](#0-1) [3](#0-2) [1](#0-0) [4](#0-3)

### Citations

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
