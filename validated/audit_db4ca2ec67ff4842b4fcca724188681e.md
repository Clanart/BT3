## Valid vulnerability confirmed — but the exact mechanics differ from the proof idea

### Title
Shared mutable `this.state` in `SignInStore` causes cross-flow account delivery when two OAuth attempts overlap - (File: `app/src/lib/stores/sign-in-store.ts`)

### Summary
The exact scenario proposed in the question — "`resolveOAuthRequest` resolves the *first* promise with an account from the *second* request's code" — does not occur, because the CSRF `state` comparison in `resolveOAuthRequest` (`this.state.oauthState.state !== action.state`) is checked against whichever oauth attempt is *currently* stored, and a deep link only passes that check if its `state` matches the currently-active `oauthState`. However, tracing the code further reveals a real, closely-related bug with the same root cause (a single mutable `this.state` field shared across concurrent in-flight attempts): the *direction* is reversed from what was hypothesized — a stale/first flow's fetched `Account` can end up delivered to the *second* flow's `resultCallback`.

### Finding Description
`SignInStore` keeps exactly one mutable field, `this.state` [1](#0-0) , and `authenticateWithBrowser` stores the CSRF `state`, `endpoint`, `onAuthCompleted`/`onAuthError` for the in-flight attempt directly inside that single object [2](#0-1) . There is no per-attempt isolation (e.g. a map keyed by CSRF token); a second `authenticateWithBrowser()` call before the first completes fully replaces `this.state.oauthState` with the new attempt's data.

The critical flaw is in `resolveOAuthRequest`: it validates `action.state` against `this.state.oauthState.state` *before* the network awaits, but after `await requestOAuthToken(...)` and `await fetchUser(...)` complete, it re-reads the **live** `this.state.oauthState.onAuthCompleted` again instead of using a value captured before the awaits:

```ts
const { endpoint } = this.state
const token = await requestOAuthToken(endpoint, action.code)
if (token) {
  const account = await fetchUser(endpoint, token)
  this.state.oauthState.onAuthCompleted(account)   // live re-read after await
}
``` [3](#0-2) 

If, during the network round trip for the first deep link's `resolveOAuthRequest` call, a *second* `authenticateWithBrowser()` invocation runs to completion and replaces `this.state` (which is possible — `authenticateWithBrowser` does not check for or block a concurrent in-flight `oauthState` before overwriting it, at line [4](#0-3) ), then `this.state.oauthState.onAuthCompleted` at the end of the first call now points to the **second** attempt's `resolve` function. This resolves the **second** flow's promise with the **Account fetched from the first flow's `code`/`endpoint`**.

Additionally, `emitAuthenticate` reads `this.state?.resultCallback` live rather than using a captured value [5](#0-4) , and the `.then()` success handler only checks `this.state.kind === Authentication`, not that the resolved account's CSRF token still matches the current `oauthState` [6](#0-5)  (unlike the `.catch()` handler, which does perform that check at [7](#0-6) ). This is an inconsistency: error handling validates the token still matches the live session, but the success handling does not.

### Impact Explanation
If two OAuth sign-in attempts are in flight at once (e.g. a stalled/abandoned first attempt whose browser tab is later completed, followed or overlapped by a second attempt started without calling `reset()`), the account resolved from the *first* attempt's authorization code can be delivered to the *second* attempt's `resultCallback`/`onDidAuthenticate` consumer. This can result in an account being silently bound to the wrong sign-in flow context — e.g. a GitHub Enterprise sign-in dialog receiving/attaching an account that was actually authenticated through a different flow, or a callback resolving with an account the user did not intend to associate at that step. This matches the general "unauthorized OAuth or account binding" impact class.

### Likelihood Explanation
This requires two `authenticateWithBrowser()` invocations to be in flight concurrently without an intervening `reset()`, plus a network-timing window during the `await requestOAuthToken`/`await fetchUser` calls in the first flow's `resolveOAuthRequest`. Note the CSRF `state` value is a `crypto.randomUUID()` [8](#0-7) , unguessable to a remote attacker who has not observed it, so an external attacker cannot arbitrarily forge a matching deep link for either flow's `state`. Exploitation therefore realistically depends on the app itself allowing overlapping sign-in attempts (e.g., a UI/dispatcher bug enabling `authenticateWithBrowser` to be called twice, such as a double-click race or a second "Add account" action while a first is still pending) rather than a purely remote, unprivileged attacker forging both deep links from scratch. This lowers likelihood relative to the question's framing of a purely "attacker-triggered" race, since the attacker does not control invocation of `authenticateWithBrowser` itself.

### Recommendation
- Capture the specific `oauthState` object (not `this.state`) at the top of `resolveOAuthRequest`, and use that captured reference's `onAuthCompleted`/`onAuthError` after the awaits, instead of re-reading `this.state.oauthState` live.
- In `emitAuthenticate` and the `authenticateWithBrowser().then()` handler, use closures over the specific `resultCallback`/`csrfToken` captured when the attempt began, not the live `this.state`.
- Have `authenticateWithBrowser` reject/cancel any pre-existing in-flight `oauthState` (call `reset()` semantics) before starting a new attempt, so at most one OAuth attempt can ever be pending at a time.

### Proof of Concept
1. Call `signInStore.authenticateWithBrowser()` (flow A) — `this.state.oauthState` = `{ state: tokenA, endpoint: A, onAuthCompleted: resolveA }`.
2. Deliver a deep link for flow A: `resolveOAuthRequest({ name: 'oauth', code: 'codeA', state: tokenA })`. Execution pauses at `await requestOAuthToken(...)`.
3. Before that await resolves, call `signInStore.authenticateWithBrowser()` again (flow B) without calling `reset()` — this overwrites `this.state.oauthState` to `{ state: tokenB, endpoint: B, onAuthCompleted: resolveB }`.
4. Let flow A's `requestOAuthToken`/`fetchUser` complete. The code at line 353 executes `this.state.oauthState.onAuthCompleted(account)`, which now calls `resolveB(accountA)` — resolving flow B's promise (and eventually flow B's `resultCallback`) with the account obtained from flow A's authorization code. [9](#0-8)

### Citations

**File:** app/src/lib/stores/sign-in-store.ts (L156-159)
```typescript
export class SignInStore extends TypedBaseStore<SignInState | null> {
  private state: SignInState | null = null

  private accounts: ReadonlyArray<Account> = []
```

**File:** app/src/lib/stores/sign-in-store.ts (L172-176)
```typescript
  private emitAuthenticate(account: Account) {
    const event: IAuthenticationEvent = { account }
    this.emitter.emit('did-authenticate', event)
    this.state?.resultCallback({ kind: 'success', account })
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
