### Title
Cross-Session OAuth Account Binding via Stale `oauthState` Read After Await in `SignInStore.resolveOAuthRequest` - (File: app/src/lib/stores/sign-in-store.ts)

### Summary
`SignInStore` uses a single mutable field, `this.state.oauthState`, to represent the "reserved" sign-in session that a pending OAuth code-exchange belongs to. The field is validated once (state/CSRF token match) *before* the asynchronous token exchange, but the completion callback is invoked by re-reading the *live* `this.state.oauthState` *after* two `await`s, rather than using a value captured and re-validated at that point. If a second, unrelated sign-in flow starts while the first exchange is still in flight, the second flow's `this.state.oauthState` silently overwrites the first, and the first flow's resolved account gets delivered to the second flow's callback — binding the wrong account/endpoint to the user's active sign-in session. This mirrors the reported smart-contract bug class: a shared "reserved" value (`currentWithheldETH` / `oauthState`) is checked once but mutated by a second concurrent operation before the first operation's effect is applied, so the first operation's action is executed against stale/foreign state.

### Finding Description
`authenticateWithBrowser` creates one `Promise<Account>` per sign-in attempt and stores its `resolve`/`reject` in `this.state.oauthState.onAuthCompleted` / `onAuthError`, keyed by a freshly generated `csrfToken`: [1](#0-0) 

When the OS delivers the OAuth deep link, `resolveOAuthRequest` validates the incoming `action.state` against `this.state.oauthState.state`, then performs two awaited network calls before invoking the completion callback: [2](#0-1) 

The critical flaw is that `this.state.oauthState.onAuthCompleted(account)` (line 353) re-reads the **live** `this.state` object rather than the specific `oauthState` reference that was validated at the top of the function (lines 337–346). Between the state/CSRF check and this final call, `requestOAuthToken` and `fetchUser` both `await` — giving the event loop a chance to run other code. If, during that window, the user (or a slow/attacker-controlled Enterprise endpoint delaying its own callback and thereby increasing this window) triggers another `authenticateWithBrowser()` call for a different endpoint, `this.state.oauthState` is unconditionally overwritten with a **new** `csrfToken`/`endpoint`/`onAuthCompleted` for the new session: [3](#0-2) 

When the first (now-stale) exchange finally completes, it calls the **second** session's `onAuthCompleted` with the **first** session's account — resolving the wrong `Promise<Account>`. That promise's `.then` handler only checks that `this.state.kind === SignInStep.Authentication` — it never verifies that the resolving session's CSRF token still matches the one it originally captured (`csrfToken`), unlike the `.catch` handler a few lines below, which *does* perform that check: [4](#0-3) 

This asymmetry (success path missing the same-session guard that the error path has) is why the existing guard does not stop the path: the check that exists (`this.state.kind !== SignInStep.Authentication`) only verifies that *some* Authentication flow is active, not that it is *the same* flow whose token was just exchanged.

### Impact Explanation
This corrupts `SignInStore`'s notion of "which account belongs to which in-progress sign-in session" — the exact analog of `currentWithheldETH` being overcommitted across two `unstake()` calls. The concrete effect is unauthorized/incorrect OAuth account binding: an account resolved from one endpoint's OAuth exchange (potentially an attacker-controlled Enterprise endpoint that intentionally stalls its `/login/oauth/access_token` and `/user` responses to widen the race window, since the attacker fully controls both the timing and the returned account JSON from `fetchUser`) can be delivered into a completely different, concurrently-active sign-in session (e.g., a legitimate github.com sign-in), causing Desktop to authenticate the user's session with the attacker-chosen account instead of the one the user actually completed authentication for. This falls under "unauthorized OAuth or account binding" impact.

### Likelihood Explanation
Exploitation requires two overlapping `authenticateWithBrowser()` invocations plus a network-timing window between the CSRF check and the final callback invocation. This window is normally short but is fully attacker-controllable when one of the two endpoints is an Enterprise instance the attacker operates (attacker can delay their `/login/oauth/access_token` or `/user` responses arbitrarily), making the race practically triggerable rather than purely theoretical. It still requires the victim to have (or be induced into) two sign-in attempts overlapping, which lowers likelihood somewhat, but the missing session-identity check on the success path is a genuine, reachable logic bug with local code evidence.

### Recommendation
In `resolveOAuthRequest`, capture the specific `oauthState` object (or its `state` token) locally before the `await`s, and re-validate that `this.state.oauthState === capturedOauthState` (or `this.state.oauthState?.state === capturedState`) immediately before invoking `onAuthCompleted`/`onAuthError`. Additionally, add the same "session hasn't changed" check used in the `.catch` handler of `authenticateWithBrowser` to its `.then` handler, comparing `this.state.oauthState?.state === csrfToken` before calling `emitAuthenticate`/transitioning to `Success`.

### Proof of Concept
1. User clicks "Sign in with browser" for a malicious/attacker-controlled Enterprise endpoint `E1` → `authenticateWithBrowser` sets `oauthState = { state: csrfToken1, endpoint: E1, onAuthCompleted: resolve1 }` and opens the browser.
2. User completes the OAuth prompt on `E1` quickly; the deep link fires `resolveOAuthRequest({ state: csrfToken1, code: code1 })`. The state check passes; `requestOAuthToken(E1, code1)` is issued — the attacker's `E1` server intentionally delays this response.
3. While that request is pending, the user (or the app, e.g. via a "sign in to GitHub.com" prompt shown elsewhere in the UI) triggers `authenticateWithBrowser()` again for `github.com` (`E2`), which overwrites `this.state.oauthState` to `{ state: csrfToken2, endpoint: E2, onAuthCompleted: resolve2 }`.
4. The attacker's `E1` server finally responds; `resolveOAuthRequest`'s pending call resolves `token`/`account` (attacker-crafted account data from `E1`), then executes `this.state.oauthState.onAuthCompleted(account)` — but `this.state.oauthState` is now `E2`'s, so `resolve2` is called with the attacker's `E1` account.
5. The `E2` flow's `.then` handler only checks `this.state.kind === Authentication` (true), so it calls `emitAuthenticate(attackerAccount)` and transitions to `Success` — binding the attacker's account to what the user believed was a `github.com` sign-in.

Note: I could not find an additional guard in `app-store.ts`'s `_resolveOAuthRequest` wrapper (only two references to it exist, in `dispatcher.ts` and `app-store.ts`, and I was unable to confirm from the index whether it adds any extra session-matching check before forwarding to `SignInStore.resolveOAuthRequest`); if such a check exists there, it should be verified directly in a full checkout since the index may not include the complete file contents.

### Citations

**File:** app/src/lib/stores/sign-in-store.ts (L260-303)
```typescript
  public async authenticateWithBrowser() {
    const currentState = this.state

    if (
      currentState?.kind !== SignInStep.Authentication &&
      currentState?.kind !== SignInStep.ExistingAccountWarning
    ) {
      const stepText = currentState ? currentState.kind : 'null'
      return fatalError(
        `Sign in step '${stepText}' not compatible with browser authentication`
      )
    }

    this.setState({ ...currentState, loading: true })

    if (currentState.kind === SignInStep.ExistingAccountWarning) {
      const { existingAccount } = currentState
      // Try to avoid emitting an error out of AccountsStore if the account
      // is already gone.
      if (this.accounts.find(x => x.endpoint === existingAccount.endpoint)) {
        await this.accountStore.removeAccount(existingAccount)
      }
    }

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
