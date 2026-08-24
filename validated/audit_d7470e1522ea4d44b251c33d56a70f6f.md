## Answer

No vulnerability found for this question.

**Analysis:**

The `endpoint` value used in `SignInStore.resolveOAuthRequest` is **not** attacker-controlled. It is read from `this.state.endpoint`, which is set locally by the app before the OAuth flow starts — either via `beginDotComSignIn` (hardcoded to `getDotComAPIEndpoint()`) or via `setEndpoint`, which requires the user to manually type/confirm an Enterprise URL that is then validated with `validateURL` and normalized with `getEnterpriseAPIURL` [1](#0-0) , [2](#0-1) .

The deep-link-derived `IOAuthAction` only carries `code` and `state` — it has no `endpoint` field at all, so an attacker crafting a malicious `x-github-client://oauth?...` link cannot inject or override which endpoint the token exchange targets [3](#0-2) , [4](#0-3) . Additionally, `action.state` must exactly match the locally generated CSRF token (`this.state.oauthState.state`) before the flow proceeds, preventing an attacker from hijacking or redirecting a different sign-in session [5](#0-4) .

On the storage side, `AccountsStore.addAccount` keys accounts strictly by `endpoint` in a `Map<string, Account>`, so adding/replacing an account for endpoint A can never overwrite the entry stored for a different endpoint B [6](#0-5) . `getAccountForEndpoint` is a pure read-only lookup (`accounts.find(a => a.endpoint === endpoint)`) used elsewhere for looking up accounts by endpoint — it plays no role in account creation or overwrite logic [7](#0-6) .

Because the endpoint is fixed by the app/user-driven flow (not by attacker-supplied `code`/`state`), and because account storage is partitioned by endpoint rather than by login/identity, there is no mechanism by which a look-alike Enterprise endpoint's OAuth `code` could cause a resulting account to bind to or overwrite the credential entry of a different endpoint's account. The scenario described in the question isn't reachable given these existing checks.

### Citations

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

**File:** app/src/lib/stores/sign-in-store.ts (L394-437)
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
```

**File:** app/src/lib/parse-app-url.ts (L4-8)
```typescript
export interface IOAuthAction {
  readonly name: 'oauth'
  readonly code: string
  readonly state: string
}
```

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

**File:** app/src/lib/stores/accounts-store.ts (L116-126)
```typescript
    const accountsByEndpoint = this.accounts.reduce(
      (map, x) => map.set(x.endpoint, x),
      new Map<string, Account>()
    )
    accountsByEndpoint.set(account.endpoint, account)

    this.accounts = sortAccounts([...accountsByEndpoint.values()])

    this.save()
    return account
  }
```

**File:** app/src/lib/api.ts (L2349-2355)
```typescript
/** Get the account for the endpoint. */
export function getAccountForEndpoint(
  accounts: ReadonlyArray<Account>,
  endpoint: string
): Account | null {
  return accounts.find(a => a.endpoint === endpoint) || null
}
```
