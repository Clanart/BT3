## Verdict: Valid, but with an important nuance on exploitability

The claim is accurate as stated for the code path shown, but the "second attacker with different code" scenario described in the question is not itself the exploitable part — the actual issue is that a failed OAuth exchange does not invalidate the pending `oauthState`/`csrfToken`, leaving the CSRF-protection nonce reusable.

### Finding Description

`resolveOAuthRequest` checks `this.state.oauthState.state !== action.state` before proceeding, and only clears the pending OAuth session (moving to `SignInStep.Success`, thereby dropping `oauthState`) when `onAuthCompleted` succeeds: [1](#0-0) 

On failure (`token` falsy), it calls `this.state.oauthState.onAuthError(...)`, which is the `reject` callback tied to the pending promise created in `authenticateWithBrowser`: [2](#0-1) 

The `.catch` handler for that promise does **not** clear or rotate `oauthState`/`csrfToken` — it re-spreads the existing `this.state` (which still contains the same `oauthState` object with the same `state` value) and only updates `error`/`loading`: [3](#0-2) 

Because `oauthState` is only removed on success (transition to `SignInStep.Success`) or on an explicit `reset()` call, a failed exchange leaves the store sitting in `SignInStep.Authentication` with the identical `csrfToken` valid indefinitely (until the user manually cancels/restarts sign-in). A subsequent `x-github-client://oauth` deep link carrying the same `state` value will pass the equality check in `resolveOAuthRequest` and proceed to call `requestOAuthToken(endpoint, action.code)` again with a different `code`.

### Impact Explanation

This turns the OAuth CSRF-protection nonce into a multi-use token instead of a single-use one. Combined with an attacker who can get a victim to click a crafted `x-github-client://oauth?...&state=<captured-state>&code=<attacker-code>` deep link (e.g., via a webpage/email link on a machine with GitHub Desktop's URL scheme registered) while a legitimate sign-in flow is stalled on an error, the attacker's authorization `code` could be exchanged and bound to the victim's GitHub Desktop instance — an OAuth account-substitution/binding issue. It does not grant remote code execution but is a legitimate authentication-integrity flaw.

### Likelihood Explanation

Exploitation requires: (1) the attacker to have or guess the current CSRF `state` value (the question's premise assumes capture via browser history/proxy log, which is a real but non-trivial precondition since state is a `crypto.randomUUID()`), and (2) the local sign-in flow to be in a stalled error state where `oauthState` is still live. This narrows practical likelihood, but the root cause — no invalidation of the pending OAuth session on failure — is a genuine logic gap, independent of how the state value was obtained.

### Recommendation
Clear (`undefined`) the `oauthState`/rotate `csrfToken`, or fully reset the `Authentication` state, whenever `onAuthError` fires in `resolveOAuthRequest`'s failure branch and in the `.catch` handler of `authenticateWithBrowser`, so a given `state`/`csrfToken` can only ever be consumed once, matching standard OAuth CSRF nonce semantics.

### Proof of Concept
1. Call `signInStore.authenticateWithBrowser()` — store enters `Authentication` with `oauthState.state = csrfToken`.
2. Call `resolveOAuthRequest({ state: csrfToken, code: 'bad-code' })`. `requestOAuthToken` returns falsy → `onAuthError` fires → `.catch` sets `{ ...this.state, error, loading:false }`, but `oauthState` (same `csrfToken`) is preserved. [4](#0-3) 
3. Call `resolveOAuthRequest({ state: csrfToken, code: 'different-code' })` again — the state-equality check at line 341 still passes, and `requestOAuthToken(endpoint, 'different-code')` is invoked a second time. [5](#0-4)

### Citations

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

**File:** app/src/lib/stores/sign-in-store.ts (L318-329)
```typescript
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
