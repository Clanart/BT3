### Title
Stale-state OAuth account confusion in `SignInStore#resolveOAuthRequest` — an in-flight sign-in flow can complete against the wrong `oauthState` after being superseded - (File: `app/src/lib/stores/sign-in-store.ts`)

### Summary
`SignInStore#resolveOAuthRequest` validates the CSRF-style `state` parameter of an incoming OAuth deep link against `this.state.oauthState.state` *before* performing the async token exchange, but then re-reads the live `this.state` object *after* the `await` calls to complete the flow, instead of using the state snapshot it validated against. If the live sign-in state has been replaced in the interim (e.g. the user cancels/restarts sign-in, or a second sign-in flow is initiated), the callbacks belonging to the *new* flow are invoked with the account resolved from the *old* flow's authorization code.

### Finding Description
`resolveOAuthRequest` is invoked from `dispatchURLAction` whenever the app receives an `oauth://` deep link [1](#0-0) , which is attacker-influenceable input: any external process, malicious webpage, or malicious redirect can register/trigger the `x-github-client` (or equivalent) protocol handler with an arbitrary `code` and `state` value, as parsed by `parseAppURL` [2](#0-1) .

The function performs its CSRF/session check up front:
```
if (!this.state || this.state.kind !== SignInStep.Authentication) return
if (!this.state.oauthState) return
if (this.state.oauthState.state !== action.state) { ...; return }
const { endpoint } = this.state
const token = await requestOAuthToken(endpoint, action.code)
if (token) {
  const account = await fetchUser(endpoint, token)
  this.state.oauthState.onAuthCompleted(account)
}
``` [3](#0-2) 

Note that `this.state` is *not* captured into a local variable before the two `await`s (`requestOAuthToken`, `fetchUser`). Instead, `this.state.oauthState.onAuthCompleted(account)` is evaluated against the store's *current* live state after both network round-trips complete. Meanwhile, `SignInStore.setState` is a simple field assignment with no versioning/ownership guard: `beginDotComSignIn`, `beginEnterpriseSignIn`, and `reset()` all overwrite `this.state` unconditionally whenever a new sign-in attempt starts [4](#0-3) .

`authenticateWithBrowser` sets a brand-new `oauthState` (with a new `csrfToken` and new `resultCallback`) each time it's called [5](#0-4) , and its own completion handler only checks `this.state.kind !== SignInStep.Authentication` — it does **not** verify that `this.state.oauthState?.state` still equals the `csrfToken` closed over by that particular promise [6](#0-5) .

This mirrors exactly the class of bug in the report: a guard condition (`initialGas`/CSRF `state` match) is checked once, but the code assumes the checked invariant still holds after control flow that can be interleaved with other mutations of the same shared/global state (`initialGas`/`userFeeInfo` vs. `SignInStore.state`). Because the final use re-reads the live global instead of the validated snapshot, a state transition that occurs during the `await` window silently redirects the outcome of one flow into another.

### Impact Explanation
If a user cancels a github.com sign-in and immediately begins a new one (or a new Enterprise sign-in) while the previous OAuth deep-link exchange (`requestOAuthToken`/`fetchUser`) is still in flight — which is plausible given normal network latency and typical "my browser callback got stuck, let me retry" user behavior — the delayed exchange's `onAuthCompleted` fires against the store's *new* live `oauthState`. That resolves the new flow's `authenticateWithBrowser` promise with an `Account` belonging to the old flow's authorization code/endpoint, transitioning that new flow directly to `SignInStep.Success` and firing `onDidAuthenticate` for a user who never actually completed the new authentication. Depending on the endpoint mismatch (dotcom vs. enterprise), this can bind the wrong GitHub account/token into a sign-in flow the user believes they just performed, i.e., silent account/identity confusion driven entirely by network timing and app-level state churn — not by anything the user physically did wrong.

### Likelihood Explanation
Exploitation does not require any local/physical access, leaked credentials, or social engineering: the trigger is simply receiving two `oauth` deep-link callbacks in close succession while the sign-in store's state has been re-initialized in between (e.g. user double-clicks "Sign in", the browser is slow to redirect, they cancel and restart). Because `dispatchURLAction`/`resolveOAuthRequest` treats the OS protocol handler and the OAuth callback as trusted input sources and mutates a single un-versioned store field, the race window (two in-flight `await`s: `requestOAuthToken` + `fetchUser`) is realistic under normal, unprivileged usage rather than a contrived scenario.

### Recommendation
Capture the validated `IAuthenticationState`/`oauthState` snapshot into a local variable prior to the `await requestOAuthToken(...)`/`await fetchUser(...)` calls, and re-validate that `this.state === capturedState` (or that `this.state.oauthState?.state === capturedState.oauthState.state`) immediately before invoking `onAuthCompleted`/`onAuthError`. If the store's state has moved on, treat the callback as stale and drop it (mirroring the `.then()`/`.catch()` guards already present in `authenticateWithBrowser`, which should likewise be strengthened to compare against the closed-over `csrfToken` rather than only `this.state.kind`).

### Proof of Concept
1. Call `dispatcher.requestBrowserAuthenticationToDotcom()`, which calls `_beginDotComSignIn` then `authenticateWithBrowser`, generating `oauthState.state = csrfToken_A` [7](#0-6) .
2. Simulate slow network: delay `requestOAuthToken`/`fetchUser` (e.g. via network throttling) after the OS delivers `oauth://...&state=csrfToken_A&code=CODE_A` and `resolveOAuthRequest` passes its `state` check and starts the two `await`s.
3. Before those `await`s resolve, call `dispatcher.beginEnterpriseSignIn()` then advance to `authenticateWithBrowser` again, which calls `signInStore.reset()` (rejecting flow A's promise with `cancelled`, but not aborting the still-running `resolveOAuthRequest` coroutine) and creates a new `oauthState.state = csrfToken_B`.
4. When flow A's `requestOAuthToken`/`fetchUser` finally resolve, `resolveOAuthRequest` executes `this.state.oauthState.onAuthCompleted(account)` against the *live* state, which is now flow B's `oauthState`/`resultCallback` — resolving flow B's promise with the account fetched using flow A's authorization code/endpoint, transitioning to `SignInStep.Success` and firing `onDidAuthenticate` for an account the user never explicitly authorized in flow B.

### Citations

**File:** app/src/ui/dispatcher/dispatcher.ts (L1761-1766)
```typescript
  public requestBrowserAuthenticationToDotcom(
    resultCallback?: (result: SignInResult) => void
  ) {
    this.appStore._beginDotComSignIn(resultCallback)
    this.requestBrowserAuthentication()
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2100-2116)
```typescript
  public async dispatchURLAction(action: URLActionType): Promise<void> {
    switch (action.name) {
      case 'oauth':
        await this.appStore._resolveOAuthRequest(action)

        if (__DARWIN__) {
          // workaround for user reports that the application doesn't receive focus
          // after completing the OAuth signin in the browser
          const isFocused = await isWindowFocused()
          if (!isFocused) {
            log.info(
              `refocusing the main window after the OAuth flow is completed`
            )
            window.focus()
          }
        }
        break
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

**File:** app/src/lib/stores/sign-in-store.ts (L212-253)
```typescript
  public reset() {
    const currentState = this.state
    this.state?.resultCallback({ kind: 'cancelled' })
    this.setState(null)

    if (currentState?.kind === SignInStep.Authentication) {
      currentState.oauthState?.onAuthError(new Error('cancelled'))
    }
  }

  /**
   * Initiate a sign in flow for github.com. This will put the store
   * in the Authentication step ready to receive user credentials.
   */
  public beginDotComSignIn(resultCallback?: (result: SignInResult) => void) {
    const endpoint = getDotComAPIEndpoint()

    if (this.state !== null) {
      this.reset()
    }

    const existingAccount = this.accounts.find(isDotComAccount)

    if (existingAccount) {
      this.setState({
        kind: SignInStep.ExistingAccountWarning,
        endpoint,
        existingAccount,
        error: null,
        loading: false,
        resultCallback: resultCallback ?? noop,
      })
    } else {
      this.setState({
        kind: SignInStep.Authentication,
        endpoint,
        error: null,
        loading: false,
        resultCallback: resultCallback ?? noop,
      })
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

**File:** app/src/lib/stores/sign-in-store.ts (L332-358)
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
```
