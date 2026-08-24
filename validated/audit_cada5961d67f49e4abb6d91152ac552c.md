## Title
Cross-session account binding via stale `this.state` re-read after `await` in `resolveOAuthRequest` — (`app/src/lib/stores/sign-in-store.ts`)

## Summary
`SignInStore.resolveOAuthRequest` validates the OAuth CSRF `state` against the *live* `this.state.oauthState` **before** the network awaits, but then re-reads the (possibly-changed) `this.state` **after** the awaits to deliver the result. If a second, unrelated sign-in/credential-helper flow becomes active while the first flow's token exchange is still in-flight, the first flow's resolved `Account` gets delivered into the *second* flow's `onAuthCompleted`/`resultCallback`, silently binding an account to a context/endpoint the user did not intend.

## Finding Description
`resolveOAuthRequest` performs its one and only authorization check synchronously at the top of the function: [1](#0-0) 

It then awaits two network calls (`requestOAuthToken`, `fetchUser`) and, only after they resolve, dereferences `this.state.oauthState` **again**, live, instead of using a value captured before the `await`s: [2](#0-1) 

Because `this.state` is mutated by `setState`/`reset()` any time a new sign-in flow starts (`beginDotComSignIn`, `beginEnterpriseSignIn`, or a second `authenticateWithBrowser` call such as the one driven by `trampolineUIHelper.promptForGitHubSignIn` for credential-helper prompts), a second flow can become "current" while the first flow's `await` is still pending: [3](#0-2) [4](#0-3) 

At that point, `this.state.oauthState.onAuthCompleted` no longer refers to the resolver of the original (CSRF-verified) Promise — it refers to the resolver captured in the *new* flow's `oauthState` (set up in `authenticateWithBrowser`): [5](#0-4) 

So the account fetched using flow #1's authorization code resolves flow #2's `Promise`. Flow #2's `.then()` handler only checks that `this.state.kind === SignInStep.Authentication` (true, since flow #2 is now the live authentication step) — it has no way to detect that the resolved account actually came from a different session/CSRF state, and proceeds to call `emitAuthenticate` and invoke **flow #2's `resultCallback`** with flow #1's account: [6](#0-5) [7](#0-6) 

The per-invocation CSRF `state` value is only checked once, at the very top of `resolveOAuthRequest`, and never re-validated against the state object that is actually used to deliver the result.

## Impact Explanation
`resultCallback`/`onAuthCompleted` for a sign-in flow is context-bound: e.g. `trampolineUIHelper.promptForGitHubSignIn(endpoint)` creates a callback tied to a *specific* git remote/endpoint that needs credentials for an in-progress git operation, resolving an `IGitAccount` used for that operation: [4](#0-3) 

If a stale/older OAuth completion (e.g. from a previously abandoned or superseded sign-in attempt) lands its result into a newer, unrelated sign-in/credential prompt because of this race, the app can silently attach the wrong `Account` to the wrong context — e.g. finishing a credential-helper prompt for one endpoint/repo with an account that was actually authorized for a different endpoint/session. This is exactly the "unauthorized-account-binding" outcome, achieved purely through timing of user-driven (or externally triggered) sign-in flows and deep-link delivery, without any explicit UI confirmation that the two sessions matched.

## Likelihood Explanation
Exploitation requires two sign-in/credential-prompt flows to overlap in a narrow window: flow #1's `resolveOAuthRequest` must be paused between its CSRF check and its `await` completing (network latency to `requestOAuthToken`/`fetchUser`) while flow #2 reaches the point where `authenticateWithBrowser` has installed a new `oauthState`. This is plausible in real usage: GitHub Desktop's credential-helper flow (`trampolineUIHelper.promptForGitHubSignIn`) can trigger sign-in popups automatically during git operations, and a user could also cancel/retry a sign-in while a prior OAuth callback is still resolving in the background from a slow network. The window is real but requires specific timing, so likelihood is moderate rather than trivially reliable.

## Recommendation
Capture the `oauthState` object (or at least its `state` token and callbacks) into a local variable **before** the `await`s in `resolveOAuthRequest`, and use that captured reference (not a fresh `this.state` read) to invoke `onAuthCompleted`/`onAuthError`. Additionally, re-verify after the awaits that `this.state?.oauthState === capturedOauthState` (identity, not just CSRF token equality) before delivering the result, discarding it otherwise:

```ts
const oauthState = this.state.oauthState
const { endpoint } = this.state
const token = await requestOAuthToken(endpoint, action.code)
...
if (this.state?.oauthState !== oauthState) return // session changed, discard
oauthState.onAuthCompleted(account) // or onAuthError
```

## Proof of Concept
A focused unit test in `app/test/unit/sign-in-store-test.ts` (existing test file, no coverage of this race today) can demonstrate the bug:
1. Call `beginDotComSignIn(cbA)`, then `authenticateWithBrowser()` (flow A gets `oauthState.state = csrfA`), with `requestOAuthToken`/`fetchUser` mocked to return a deferred/pending Promise (so it doesn't resolve immediately).
2. Call `resolveOAuthRequest({ state: csrfA, code: 'codeA' })` — passes the CSRF check, then blocks on the mocked pending `requestOAuthToken`.
3. Before that promise resolves, call `beginDotComSignIn(cbB)` then `authenticateWithBrowser()` again (flow B, `oauthState.state = csrfB`).
4. Resolve the mocked `requestOAuthToken`/`fetchUser` from step 1 with `accountA`.
5. Observe that `cbB` (flow B's `resultCallback`), not `cbA`, is invoked with `{ kind: 'success', account: accountA }` — demonstrating that flow A's account was delivered into flow B's context despite never passing flow B's CSRF check. [8](#0-7)

### Citations

**File:** app/src/lib/stores/sign-in-store.ts (L172-176)
```typescript
  private emitAuthenticate(account: Account) {
    const event: IAuthenticationEvent = { account }
    this.emitter.emit('did-authenticate', event)
    this.state?.resultCallback({ kind: 'success', account })
  }
```

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

**File:** app/src/lib/stores/sign-in-store.ts (L286-303)
```typescript
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

**File:** app/src/lib/trampoline/trampoline-ui-helper.ts (L80-99)
```typescript
  public promptForGitHubSignIn(endpoint: string): Promise<Account | undefined> {
    return new Promise<Account | undefined>(async resolve => {
      const cb = (result: SignInResult) => {
        resolve(result.kind === 'success' ? result.account : undefined)
        this.dispatcher.closePopup(PopupType.SignIn)
      }

      const { hostname, origin } = new URL(endpoint)
      if (hostname === 'github.com') {
        this.dispatcher.beginDotComSignIn(cb)
      } else {
        this.dispatcher.beginEnterpriseSignIn(cb)
        await this.dispatcher.setSignInEndpoint(origin)
      }

      this.dispatcher.showPopup({
        type: PopupType.SignIn,
        isCredentialHelperSignIn: true,
        credentialHelperUrl: endpoint,
      })
```
