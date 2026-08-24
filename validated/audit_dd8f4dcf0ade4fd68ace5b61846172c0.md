## Title
TOCTOU in `SignInStore.resolveOAuthRequest` allows a stale/attacker-controlled OAuth completion to be bound to an unrelated, later sign-in session - (File: `app/src/lib/stores/sign-in-store.ts`)

### Summary
The scenario as literally proposed by the question (a *second* deep link arriving strictly *after* `reset()` already ran, using the old csrf token) is **not exploitable**, because `resolveOAuthRequest` re-checks `this.state` synchronously at the top of the function and bails out immediately if `this.state` is `null` or not `Authentication`, or if `oauthState.state !== action.state`. [1](#0-0) 

However, a closely related and real bug exists in the same function: after the initial synchronous validation, the function `await`s two network calls and then dereferences `this.state` **again, live**, instead of using the local snapshot that was validated before the `await`. If the sign-in session changes during that `await` window (e.g. the user cancels via `reset()` and then starts a brand-new sign-in flow), the completion callback that ultimately fires is the **new** flow's `resolve`/`reject`, invoked with data obtained from the **old** (already-validated) OAuth exchange.

### Finding Description
```ts
public async resolveOAuthRequest(action: IOAuthAction) {
  if (!this.state || this.state.kind !== SignInStep.Authentication) return
  if (!this.state.oauthState) return
  if (this.state.oauthState.state !== action.state) { ... return }

  const { endpoint } = this.state
  const token = await requestOAuthToken(endpoint, action.code)   // <-- await #1

  if (token) {
    const account = await fetchUser(endpoint, token)             // <-- await #2
    this.state.oauthState.onAuthCompleted(account)   // <-- re-reads live this.state!
  } else {
    this.state.oauthState.onAuthError(...)            // <-- same issue
  }
}
``` [2](#0-1) 

All the security-relevant checks (state kind, `oauthState` presence, csrf `state` match) are performed *once*, before the two `await`s. `endpoint` is captured into a local, but the `oauthState` object (and thus the `onAuthCompleted`/`onAuthError` callbacks) is **not** captured into a local — it is read again from `this.state` after both awaits complete.

Meanwhile, `SignInStore.reset()` unconditionally nulls `this.state` and invokes the *old* `onAuthError`:
```ts
public reset() {
  const currentState = this.state
  this.state?.resultCallback({ kind: 'cancelled' })
  this.setState(null)
  if (currentState?.kind === SignInStep.Authentication) {
    currentState.oauthState?.onAuthError(new Error('cancelled'))
  }
}
``` [3](#0-2) 

If, while an earlier `resolveOAuthRequest` call is still awaiting `requestOAuthToken`/`fetchUser`, the user (or a UI flow) calls `reset()` and then starts a **new** sign-in (`beginDotComSignIn`/`beginEnterpriseSignIn` → `authenticateWithBrowser`), a fresh `oauthState` with a new csrf token and new `resolve`/`reject` closures is installed on `this.state`. When the stale `resolveOAuthRequest` call finally resumes, `this.state.oauthState.onAuthCompleted(account)` fires the **new** flow's `resolve` with the `account` object obtained from the **old** flow's endpoint/token exchange — an account the new flow never validated via its own csrf token.

This requires an OAuth endpoint (e.g., an Enterprise instance) whose token/user endpoints are attacker-controlled or attacker-delayable, so the attacker can hold the `await` open long enough for the victim to cancel and start a second, unrelated sign-in.

### Impact Explanation
If achievable, this results in an attacker-crafted `Account` (fetched from an attacker-controlled/malicious OAuth endpoint) being silently bound to a sign-in flow the user believed was directed at a different (legitimate) endpoint, via `emitAuthenticate(account)` and the resulting `Success` state — a form of account-binding confusion. The severity is bounded by the fact that the resulting `Account.endpoint` still reflects the original (malicious) endpoint used for the token/user fetch, not the new target endpoint, so the confusion is at the "which sign-in flow completed" UX/state level rather than a full cross-endpoint credential swap.

### Likelihood Explanation
Low. It requires: (1) the user to initiate sign-in against an attacker-controlled/malicious OAuth endpoint, (2) that endpoint delaying its token/user-info responses for an attacker-chosen window, (3) the user cancelling and starting a second, different sign-in during exactly that window, and (4) no other timeout/abort logic interrupting the pending request. This is a narrow, timing-dependent race rather than a straightforward reachable path from a single deep link, as described in the question.

### Recommendation
In `resolveOAuthRequest`, snapshot the entire `oauthState` (including `onAuthCompleted`/`onAuthError` and the csrf `state`) into local variables before the `await`s, and use those locals — not `this.state` — when resolving/rejecting. Additionally, re-verify that the live `this.state.oauthState?.state` still equals the locally captured csrf token before invoking the callback, mirroring the pattern already used defensively in `authenticateWithBrowser`'s `.catch` handler. [4](#0-3) 

### Proof of Concept
A focused unit test can demonstrate the TOCTOU:
1. Call `beginDotComSignIn()` then `authenticateWithBrowser()` to install `oauthState` with `csrfToken1`.
2. Mock `requestOAuthToken`/`fetchUser` to return a controllable, delayed `Promise` (resolved manually later) for the first call.
3. Call `resolveOAuthRequest({ state: csrfToken1, code: 'x' })` — this passes the synchronous checks and suspends at the `await`.
4. Call `reset()`, then `beginDotComSignIn()` + `authenticateWithBrowser()` again to install a second `oauthState` with `csrfToken2` and a spy `resolve2`.
5. Resolve the mocked `requestOAuthToken`/`fetchUser` promises from step 2 with an attacker-controlled account object.
6. Assert that `resolve2` (the second flow's completion callback) was invoked with the attacker-controlled account, even though its own csrf token (`csrfToken2`) was never matched against `action.state`.

Note: the exact scenario described in the question — a *second deep link delivered strictly after* `reset()` completed, replaying the old csrf token — is **not** exploitable, since the top-of-function checks in `resolveOAuthRequest` (`this.state === null` after `reset()`) cause an immediate early return in that case. [5](#0-4)

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
