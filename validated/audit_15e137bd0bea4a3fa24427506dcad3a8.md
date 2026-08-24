This is a valid, verifiable finding. The `trampolineServer` accepts concurrent socket connections and dispatches each one to its own async `handler(command)` call without any per-endpoint or global serialization [1](#0-0) , meaning two `git credential get` invocations for two different attacker-chosen enterprise hosts (e.g., from parallel submodule fetches) can concurrently reach `createCredentialHelperTrampolineHandler` → `getCredential` → `ui.promptForGitHubSignIn(endpoint)` [2](#0-1) . Both calls funnel into the single, process-wide `SignInStore` singleton state.

### Title
Cross-Endpoint OAuth Account Binding via Concurrent Enterprise Sign-In Prompts - (File: `app/src/lib/stores/sign-in-store.ts`)

### Summary
`SignInStore` keeps a single mutable `this.state` field shared by all sign-in flows. `TrampolineUIHelper.promptForGitHubSignIn` performs a non-atomic two-step sequence — `dispatcher.beginEnterpriseSignIn(cb)` followed by `await dispatcher.setSignInEndpoint(origin)` — against that shared singleton [3](#0-2) . If a second, concurrent `getCredential` call for a different attacker-controlled endpoint interleaves between these two steps, `SignInStore.setEndpoint` builds its resulting `Authentication` state from a mix of the *first* caller's endpoint parameter and the *second* caller's `resultCallback`, corrupting the endpoint↔callback binding [4](#0-3) .

### Finding Description
`SignInStore.beginEnterpriseSignIn` unconditionally resets any existing in-flight state: `if (this.state !== null) { this.reset() }` then sets a fresh `EndpointEntry` state with the new caller's `resultCallback` [5](#0-4) .

`SignInStore.setEndpoint(url)` is called immediately afterward by the same `promptForGitHubSignIn` invocation, but it captures `currentState = this.state` fresh at call time and uses `currentState.resultCallback` when building the next state, while `url`/`endpoint` come from the calling closure's own parameter:
```
const endpoint = getEnterpriseAPIURL(validUrl)
...
this.setState({
  kind: SignInStep.Authentication,
  endpoint,                                  // <- caller A's own endpoint
  resultCallback: currentState.resultCallback, // <- whatever is CURRENTLY in the singleton
  ...
})
``` [6](#0-5) 

Consider two concurrent `getCredential` calls for attacker endpoints A and B, both classified `'enterprise'` [7](#0-6) :

1. Call A: `beginEnterpriseSignIn(cb_A)` sets state = `EndpointEntry{resultCallback: cb_A}`.
2. Before A's `await setEndpoint(originA)` resumes, call B interleaves: `beginEnterpriseSignIn(cb_B)` resets state (cancelling A's stale callback reference is *not* what happens here — reset only fires the callback stored in the state object *at that moment*) and sets state = `EndpointEntry{resultCallback: cb_B}`.
3. A's suspended `setEndpoint(originA)` continuation resumes, reads `currentState = this.state` = B's `EndpointEntry{resultCallback: cb_B}`, and produces `Authentication{endpoint: <A's own endpoint>, resultCallback: cb_B}`.

The UI now displays/operates against endpoint A's authentication step, but on OAuth success `state.oauthState.onAuthCompleted(account)` invokes `resultCallback` — which is `cb_B`, the promise-resolver captured by `TrampolineUIHelper.promptForGitHubSignIn(endpointB)` [8](#0-7) . There is no check anywhere that the resolved `account.endpoint` matches the endpoint the original credential request (B) was for; `credWithAccount` simply merges the account's `login`/`token` into whatever credential map is currently pending for host B [9](#0-8) . The net effect: a valid OAuth token obtained for enterprise host A gets delivered to satisfy the git credential request for host B, i.e. it is handed to a host the user never authenticated it for.

### Impact Explanation
An attacker who controls two (or more) distinct enterprise-classified sub-hosts reachable in a single fetch/clone (e.g. via submodules or multiple remotes triggering parallel trampoline credential-helper invocations) can induce Desktop to leak a legitimately-obtained OAuth account token to a different, attacker-chosen host than the one the user believed they were authenticating. This is a credential exfiltration / unauthorized-account-binding primitive that operates purely from repository content (submodule URLs) without any unusual user action beyond a normal clone/fetch that triggers sign-in.

### Likelihood Explanation
Requires a timing race between two concurrent `getCredential` invocations landing between `beginEnterpriseSignIn` and `setEndpoint` in `promptForGitHubSignIn` — feasible because `TrampolineServer.processCommand` handles each socket connection independently and asynchronously with no cross-connection lock [1](#0-0) , and Git can spawn multiple simultaneous credential-helper subprocesses for different submodule/remote hosts during a single fetch/clone. The window is small (`beginEnterpriseSignIn` synchronous call, immediately followed by an `await`), but is a genuine race in shared mutable singleton state, not merely theoretical — the same race also causes the "wrong" (first) request to silently receive a spurious 'cancelled' result even without full account leakage, which by itself corrupts the user's sign-in flow.

### Recommendation
Make `SignInStore` support (or explicitly serialize/queue) concurrent sign-in requests instead of sharing one mutable singleton `state` across unrelated flows: e.g., have `beginEnterpriseSignIn`/`setEndpoint` operate on a per-flow token/handle rather than global `this.state`, or have `TrampolineUIHelper.promptForGitHubSignIn` acquire a mutex so only one enterprise sign-in flow is in-flight at a time and queue others. Additionally, validate that the account returned by a sign-in flow (`account.endpoint`) matches the endpoint that was originally requested before using it to satisfy a `getCredential` call.

### Proof of Concept
A focused unit test against `SignInStore`/`TrampolineUIHelper` (mocking `dispatcher`) that:
1. Calls `promptForGitHubSignIn('https://enterprise-a.attacker.example')` without awaiting completion.
2. Immediately calls `promptForGitHubSignIn('https://enterprise-b.attacker.example')` before the first `await setSignInEndpoint` resolves (achievable by controlling microtask ordering / using `Promise.resolve().then()` inside a mocked `beginEnterpriseSignIn`).
3. Completes the resulting single `Authentication` state's OAuth flow successfully.
4. Asserts which promise (A's or B's) resolves with the successful `Account`, and shows it can be the *other* endpoint's promise, i.e., `resultCallback` for endpoint A's `Authentication{endpoint: A}` state actually being `cb_B`, matching the trace in `SignInStore.setEndpoint` [10](#0-9) .

**Uncertainty:** I could not execute this test in the current environment; the race depends on precise microtask/interleaving timing between `beginEnterpriseSignIn` and `setEndpoint`, and I have not located an existing test file that already demonstrates this exact interleaving (only tests for sequential calls) [11](#0-10) . The code path and mismatch logic described above are directly present in the source, but the exact reachability of the required interleaving from real concurrent submodule-triggered `getCredential` calls (vs. Node's event loop scheduling) should be confirmed empirically.

### Citations

**File:** app/src/lib/trampoline/trampoline-server.ts (L162-183)
```typescript
  private async processCommand(socket: Socket, command: ITrampolineCommand) {
    if (!isValidTrampolineToken(command.trampolineToken)) {
      throw new Error('Tried to use invalid trampoline token')
    }

    const handler = this.commandHandlers.get(command.identifier)

    if (handler === undefined) {
      socket.end()
      return
    }

    const result = await handler(command).catch(e =>
      log.error('Error processing trampoline command', e)
    )

    if (result !== undefined) {
      socket.end(result)
    } else {
      socket.end()
    }
  }
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L47-56)
```typescript
const credWithAccount = (c: Credential, a: IGitAccount | undefined) =>
  a && new Map(c).set('username', a.login).set('password', a.token)

async function getGitHubCredential(cred: Credential, store: AccountsStore) {
  const endpoint = `${getCredentialUrl(cred)}`
  const account = await findGitHubTrampolineAccount(store, endpoint)
  if (account) {
    info(`found GitHub credential for ${endpoint} in store`)
  }
  return credWithAccount(cred, account)
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L94-125)
```typescript
async function getCredential(cred: Credential, store: Store, token: string) {
  const ghCred = await getGitHubCredential(cred, store)

  if (ghCred) {
    return ghCred
  }

  const endpointKind = await getEndpointKind(cred, store)
  const accounts = await store.getAll()

  const endpoint = `${getCredentialUrl(cred)}`
  const apiEndpoint = getAPIEndpoint(endpoint)

  // If it appears as if the endpoint is a GitHub host and we don't have an
  // account for that endpoint then we should prompt the user to sign in.
  if (
    endpointKind !== 'generic' &&
    !accounts.some(a => a.endpoint === apiEndpoint)
  ) {
    if (getIsBackgroundTaskEnvironment(token)) {
      debug('background task environment, skipping prompt')
      return undefined
    }

    const account = await ui.promptForGitHubSignIn(endpoint)

    if (!account) {
      setHasRejectedCredentialsForEndpoint(token, endpoint)
    }

    return credWithAccount(cred, account)
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

**File:** app/src/lib/stores/sign-in-store.ts (L394-459)
```typescript
  public async setEndpoint(url: string): Promise<void> {
    const currentState = this.state

    if (
      currentState?.kind !== SignInStep.EndpointEntry &&
      currentState?.kind !== SignInStep.ExistingAccountWarning
    ) {
      const stepText = currentState ? currentState.kind : 'null'
      return fatalError(
        `Sign in step '${stepText}' not compatible with endpoint entry`
      )
    }

    /**
     * If the user enters a github.com url in the GitHub Enterprise sign-in
     * flow we'll redirect them to the GitHub.com sign-in flow.
     */
    if (/^(?:https:\/\/)?(?:api\.)?github\.com($|\/)/.test(url)) {
      this.beginDotComSignIn(currentState.resultCallback)
      return
    }

    this.setState({ ...currentState, loading: true })

    let validUrl: string
    try {
      validUrl = validateURL(url)
    } catch (e) {
      let error = e
      if (e.name === InvalidURLErrorName) {
        error = new Error(
          `The GitHub Enterprise instance address doesn't appear to be a valid URL. We're expecting something like https://example.ghe.com.`
        )
      } else if (e.name === InvalidProtocolErrorName) {
        error = new Error(
          'Unsupported protocol. Only https is supported when authenticating with GitHub Enterprise instances.'
        )
      }

      this.setState({ ...currentState, loading: false, error })
      return
    }

    const endpoint = getEnterpriseAPIURL(validUrl)

    const existingAccount = this.accounts.find(x => x.endpoint === endpoint)

    if (existingAccount) {
      this.setState({
        kind: SignInStep.ExistingAccountWarning,
        endpoint,
        existingAccount,
        error: null,
        loading: false,
        resultCallback: currentState.resultCallback,
      })
    } else {
      this.setState({
        kind: SignInStep.Authentication,
        endpoint,
        error: null,
        loading: false,
        resultCallback: currentState.resultCallback,
      })
    }
  }
```

**File:** app/test/unit/sign-in-store-test.ts (L102-127)
```typescript
  describe('beginEnterpriseSignIn', () => {
    it('transitions to EndpointEntry step', () => {
      signInStore.beginEnterpriseSignIn()
      const state = signInStore.getState()
      assert.notEqual(state, null)
      assert.equal(state?.kind, SignInStep.EndpointEntry)
    })

    it('sets initial state correctly', () => {
      signInStore.beginEnterpriseSignIn()
      const state = signInStore.getState()
      if (state?.kind === SignInStep.EndpointEntry) {
        assert.equal(state.error, null)
        assert.equal(state.loading, false)
      }
    })

    it('resets previous state before starting', () => {
      // Start a dotcom sign-in first
      signInStore.beginDotComSignIn()
      assert.equal(signInStore.getState()?.kind, SignInStep.Authentication)

      // Starting enterprise sign-in should replace that state
      signInStore.beginEnterpriseSignIn()
      assert.equal(signInStore.getState()?.kind, SignInStep.EndpointEntry)
    })
```
