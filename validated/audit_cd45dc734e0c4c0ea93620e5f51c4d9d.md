## Analysis

The Sherlock finding is fundamentally about an *external call whose completion writes back into shared mutable state without a re-entrancy lock*, allowing a second in-flight invocation to interleave and corrupt what the first invocation eventually commits. The closest analog in GitHub Desktop is the OAuth callback handler in `SignInStore`, which resolves an async, network-controlled operation and then dereferences a **live, mutable** `this.state` field to decide where to deliver the result — instead of capturing the destination (the resolve/reject closures) before awaiting.

### Title
Cross-flow account/state confusion in OAuth callback due to missing re-entrancy guard - (File: `app/src/lib/stores/sign-in-store.ts`)

### Summary
`SignInStore.resolveOAuthRequest` (triggered by the `x-github-client://oauth?code=...&state=...` deep link) validates the CSRF `state` **before** awaiting two network calls (`requestOAuthToken`, `fetchUser`), then delivers the result by reading `this.state.oauthState.onAuthCompleted` **after** the awaits complete [1](#0-0) . Because `this.state` is a single mutable field on the store (not a value captured at call time), if a second sign-in flow is started while the first `resolveOAuthRequest` call is still awaiting the network, the first call will deliver its result into the *second* flow's promise, bypassing that second flow's own CSRF check entirely.

### Finding Description
`authenticateWithBrowser` creates a fresh `oauthState` object per flow, embedding `resolve`/`reject` closures for a `Promise<Account>`, and stores it on `this.state.oauthState` [2](#0-1) . The deep-link handler `resolveOAuthRequest` checks that the incoming `action.state` matches `this.state.oauthState.state` at call time, then performs two awaited network round-trips, and only *after* those complete does it call `this.state.oauthState.onAuthCompleted(account)` — re-reading `this.state` rather than using a value captured before the awaits [3](#0-2) .

If, during the window between the CSRF check and the delivery of the result, another sign-in attempt begins (`beginDotComSignIn`/`beginEnterpriseSignIn`/`authenticateWithBrowser` again), `this.state.oauthState` is overwritten with a brand-new object holding a *different* flow's `resolve`/`reject` [4](#0-3) [5](#0-4) . The pending `resolveOAuthRequest` call has no way to know its intended destination has changed — it has no local capture of the closures, no in-flight guard, and no re-validation of `action.state` against the *current* flow before delivering the account. As a result, the account/error obtained from the **first** (attacker-influenced) OAuth exchange gets silently delivered into the **second**, unrelated flow's promise, completing it without ever validating CSRF `state` for that second flow.

This is the direct analog of the reported bug: an external, attacker-timeable call (`requestOAuthToken`/`fetchUser`, backed by whatever endpoint/host is being authenticated against — including a GitHub Enterprise endpoint fully controlled by an attacker, matching the "git remote/proxy response" attacker model) completes and mutates shared state without any effects-before-interaction ordering or re-entrancy lock, unlike other flows in the same codebase which explicitly guard against concurrent re-entry (e.g. `withPushPullFetch`, `withIsCommitting` in `app-store.ts` which check and set an `isXInProgress` flag before running the awaited operation) [6](#0-5) [7](#0-6) . `SignInStore` has no equivalent guard.

The deep link itself is dispatched straight from OS-level URL handling through `Dispatcher.dispatchURLAction` into `appStore._resolveOAuthRequest(action)` [8](#0-7) , and the code even acknowledges that the callback URL can be re-delivered ("This is likely due to a browser reloading the callback URL") [9](#0-8) , confirming that re-entrant delivery of this callback is an anticipated but incompletely-handled condition.

### Impact Explanation
An attacker who controls (or can delay responses from) the endpoint being used for one sign-in attempt — e.g. a malicious/compromised GitHub Enterprise host, matching the "git remote/proxy response" attacker model — can hold the `requestOAuthToken`/`fetchUser` await open indefinitely. If the user, believing the first attempt stalled, cancels and retries sign-in (a natural user action, not an unnatural or contrived step), the delayed first exchange later resolves and is delivered into the new attempt's promise without CSRF validation against that new attempt. This lets an attacker-controlled OAuth exchange silently complete the second, otherwise-legitimate sign-in flow, resulting in unauthorized/incorrect account binding in `SignInStore` — one of the explicitly valid impact categories.

### Likelihood Explanation
Exploitation requires: (1) the user to authenticate against an endpoint the attacker controls or can slow down (e.g., a GHE instance under attacker control, plausible per the given threat model), and (2) the user or app to start a second sign-in attempt while the first is still pending — a normal "sign-in appears stuck, try again" behavior, not an artificial multi-step social-engineering scenario. No local access, malware, leaked credentials, or admin rights are required.

### Recommendation
- Capture the intended `resolve`/`reject` (and the expected `state`/`endpoint`) in local variables at the top of `resolveOAuthRequest`, before any `await`, and use only those captured references to deliver the result — never re-read `this.state` after the awaits.
- Add an explicit in-flight guard (mirroring `withPushPullFetch`/`withIsCommitting`) so a new sign-in flow cannot be started while a previous `resolveOAuthRequest` call for the same store instance is still pending, or so that a stale pending resolution is explicitly invalidated/rejected when a new flow begins.
- Re-validate `action.state` against the *current* `this.state.oauthState.state` again immediately before calling `onAuthCompleted`, not only before the network awaits.

### Proof of Concept
1. Victim starts Enterprise sign-in against attacker-controlled GHE endpoint `E1`; `SignInStore` stores `oauthState1 = { state: csrf1, onAuthCompleted: resolve1, ... }`.
2. Browser redirects to `x-github-client://oauth?code=C1&state=csrf1`; `resolveOAuthRequest` passes the `csrf1` check and calls `await requestOAuthToken(E1, C1)` — attacker's server intentionally delays this HTTP response.
3. Victim, seeing no progress, cancels and starts a fresh sign-in to `github.com`; `authenticateWithBrowser` overwrites `this.state.oauthState` with `oauthState2 = { state: csrf2, onAuthCompleted: resolve2, ... }`.
4. Attacker's server finally responds to the pending call from step 2 with an attacker-chosen `access_token`; `fetchUser` returns an attacker-chosen account.
5. `resolveOAuthRequest` executes `this.state.oauthState.onAuthCompleted(account)` — but `this.state.oauthState` is now `oauthState2`, so `resolve2(account)` fires, completing the victim's **github.com** sign-in with the attacker-chosen account, without `csrf2` ever having been checked.

### Citations

**File:** app/src/lib/stores/sign-in-store.ts (L226-253)
```typescript
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

**File:** app/src/lib/stores/sign-in-store.ts (L366-379)
```typescript
  public beginEnterpriseSignIn(
    resultCallback?: (result: SignInResult) => void
  ) {
    if (this.state !== null) {
      this.reset()
    }

    this.setState({
      kind: SignInStep.EndpointEntry,
      error: null,
      loading: false,
      resultCallback: resultCallback ?? noop,
    })
  }
```

**File:** app/src/lib/stores/app-store.ts (L5364-5391)
```typescript
  private async withIsCommitting(
    repository: Repository,
    fn: () => Promise<boolean>
  ): Promise<boolean> {
    const state = this.repositoryStateCache.get(repository)
    // ensure the user doesn't try and commit again
    if (state.isCommitting) {
      return false
    }

    this.repositoryStateCache.update(repository, () => ({
      isCommitting: true,
      hookProgress: null,
      subscribeToCommitOutput: null,
    }))
    this.emitUpdate()

    try {
      return await fn()
    } finally {
      this.repositoryStateCache.update(repository, () => ({
        isCommitting: false,
        hookProgress: null,
        subscribeToCommitOutput: null,
      }))
      this.emitUpdate()
    }
  }
```

**File:** app/src/lib/stores/app-store.ts (L5427-5450)
```typescript
  private async withPushPullFetch(
    repository: Repository,
    fn: () => Promise<void>
  ): Promise<void> {
    const state = this.repositoryStateCache.get(repository)
    // Don't allow concurrent network operations.
    if (state.isPushPullFetchInProgress) {
      return
    }

    this.repositoryStateCache.update(repository, () => ({
      isPushPullFetchInProgress: true,
    }))
    this.emitUpdate()

    try {
      await fn()
    } finally {
      this.repositoryStateCache.update(repository, () => ({
        isPushPullFetchInProgress: false,
      }))
      this.emitUpdate()
    }
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
