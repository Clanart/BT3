### Title
Spoofable `X-GitHub-Request-Id` header lets any malicious API endpoint/proxy force repeated forced sign-outs — (File: `app/src/lib/api.ts`)

### Summary
GitHub Desktop treats **any** `401 Unauthorized` HTTP response as proof that an account's OAuth token has been permanently invalidated by GitHub, as long as the response carries an `X-GitHub-Request-Id` header and lacks `X-GitHub-OTP`. This "signal" is nothing more than an arbitrary response header that any server — including a malicious/compromised GitHub Enterprise instance, or any man-in-the-middle proxy sitting between Desktop and that endpoint — can trivially set on every response. There is no cryptographic binding between that header and the real GitHub backend, so the check that was added specifically to avoid false positives from generic gateways/proxies (referencing issue #12943) can be defeated by exactly the class of attacker it was meant to guard against.

### Finding Description
`API.ghRequest` in [1](#0-0)  decides that a token is invalid using only response metadata:

```
if (
  response.status === HttpStatusCode.Unauthorized &&
  response.headers.has('X-GitHub-Request-Id') &&
  !response.headers.has('X-GitHub-OTP')
) {
  API.emitTokenInvalidated(this.endpoint, this.token)
}
```

`emitTokenInvalidated` fans this out to any registered listener [2](#0-1) . `AppStore` is one such listener, and unconditionally acts on it:

```
private onTokenInvalidated = (endpoint: string, token: string) => {
  const account = getAccountForEndpoint(this.accounts, endpoint)
  ...
  // If the token was invalidated for an account, sign out from that account
  this._removeAccount(account)
  this._showPopup({ type: PopupType.InvalidatedToken, account })
}
``` [3](#0-2) 

The invariant the code intends to enforce is: "only GitHub(.com/Enterprise) itself can tell Desktop that a token is dead." The actual invariant enforced is: "any HTTP response with status 401 and an `X-GitHub-Request-Id` header can tell Desktop that a token is dead" — and that header is attacker-controllable by anyone who can answer requests sent to the account's configured `endpoint` (every enterprise account's endpoint is just a URL string stored client-side, see `Account.endpoint` usage in `fetchUser`/`updatedAccount` at [4](#0-3) ). Anyone who can act as, or intercept traffic to, that endpoint (a rogue/compromised GitHub Enterprise Server, a malicious captive portal/corporate proxy, or an operator of a self-hosted GHE the user connected to) can simply return `401` with an `X-GitHub-Request-Id` header on every API call.

This is exactly analogous to the reported Nudge.xyz bug: a component (`Executor`/here, the HTTP response-processing path) is trusted to only be driven by a privileged party, but is actually reachable/controllable by anyone who can produce the "trigger" input, letting them force the same privileged side effect (`renounceRole` / `_removeAccount`) over and over, denying service to the legitimate flow.

### Impact Explanation
Every future `ghRequest` call made by Desktop against that endpoint (background refreshes, repo indicator updates, PR checks, etc.) gives the attacker another opportunity to fire this path, so the user is repeatedly and silently signed out of their account — a persistent denial of service against a legitimate GitHub Enterprise account inside Desktop, with the `InvalidatedToken` dialog nagging the user to re-authenticate every time [5](#0-4) . Repeated sign-outs interrupt sync, notifications, and any signed operations relying on the account, and could also be used to pressure a user into re-entering credentials against an attacker-controlled endpoint (credential phishing amplification), since the "sign in again" flow reopens the enterprise sign-in dialog pointed at that same endpoint.

### Likelihood Explanation
Any party capable of responding to (or intercepting/tampering with) HTTP requests aimed at an added GitHub Enterprise endpoint can trigger this on demand, with no credentials, no local machine access, and no user interaction beyond the account already being configured and Desktop periodically polling the API (which it does automatically, e.g. via `AccountsStore.refresh`, repository indicators, etc.). No admin rights or pre-existing malware are required — only network-position control or operation of the target endpoint, which is squarely within the "attacker controls...a git remote/proxy response" impact category.

### Recommendation
Do not rely solely on client-visible, attacker-settable response headers to decide that a token must be invalidated. At minimum:
- Require corroborating signals that are harder to spoof (e.g., only trust this signal for `github.com`/verified GHE endpoints reached over a pinned/validated TLS connection, or cross-check via a dedicated `GET /user` call before revoking).
- Rate-limit / debounce repeated invalidation events per endpoint so a single misbehaving/malicious endpoint cannot force unlimited forced sign-outs.
- Consider only acting on this signal for `api.github.com` and require additional verification (e.g., checking the response body's shape) for self-hosted GHE endpoints, since those are exactly the attacker-influenceable case.

### Proof of Concept
1. Add a GitHub Enterprise account in Desktop pointing at `https://malicious-ghe.example.com/api/v3` (attacker-controlled server, or a proxy an attacker controls sitting in front of a legitimate GHE instance).
2. Configure the server to respond to any authenticated API request with:
   - HTTP status `401`
   - Header `X-GitHub-Request-Id: anything`
   - No `X-GitHub-OTP` header
3. Wait for Desktop to make any background API call to that endpoint (e.g. `AccountsStore.refresh`, repository indicator refresh).
4. Observe `API.emitTokenInvalidated` fires, `AppStore.onTokenInvalidated` runs `_removeAccount`, and the `InvalidatedToken` popup appears, signing the user out of that account.
5. Repeat indefinitely — every subsequent request the user makes against that endpoint retriggers the same forced sign-out, since the check only inspects response headers the attacker fully controls.

### Citations

**File:** app/src/lib/api.ts (L828-839)
```typescript
  private static readonly tokenInvalidatedListeners =
    new Set<TokenInvalidatedCallback>()

  public static onTokenInvalidated(callback: TokenInvalidatedCallback) {
    this.tokenInvalidatedListeners.add(callback)
  }

  private static emitTokenInvalidated(endpoint: string, token: string) {
    this.tokenInvalidatedListeners.forEach(callback =>
      callback(endpoint, token)
    )
  }
```

**File:** app/src/lib/api.ts (L1832-1859)
```typescript
  private async ghRequest(
    method: HTTPMethod,
    path: string,
    options: {
      body?: Object
      customHeaders?: Object
      reloadCache?: boolean
    } = {}
  ): Promise<Response> {
    const response = await this.request(this.endpoint, method, path, options)

    // Only consider invalid token when the status is 401 and the response has
    // the X-GitHub-Request-Id header, meaning it comes from GH(E) and not from
    // any kind of proxy/gateway. For more info see #12943
    // We're also not considering a token has been invalidated when the reason
    // behind a 401 is the fact that any kind of 2 factor auth is required.
    if (
      response.status === HttpStatusCode.Unauthorized &&
      response.headers.has('X-GitHub-Request-Id') &&
      !response.headers.has('X-GitHub-OTP')
    ) {
      API.emitTokenInvalidated(this.endpoint, this.token)
    }

    tryUpdateEndpointVersionFromResponse(this.endpoint, response)

    return response
  }
```

**File:** app/src/lib/stores/app-store.ts (L888-911)
```typescript
  private onTokenInvalidated = (endpoint: string, token: string) => {
    const account = getAccountForEndpoint(this.accounts, endpoint)

    if (account === null) {
      return
    }

    // If we have a token for the account but it doesn't match the token that
    // was invalidated that likely means that someone held onto an account for
    // longer than they should have which is bad but what's even worse is if we
    // invalidate an active account.
    if (account.token && account.token !== token) {
      log.error(`Token for ${endpoint} invalidated but token mismatch`)
      return
    }

    // If the token was invalidated for an account, sign out from that account
    this._removeAccount(account)

    this._showPopup({
      type: PopupType.InvalidatedToken,
      account,
    })
  }
```

**File:** app/src/lib/stores/accounts-store.ts (L261-269)
```typescript
async function updatedAccount(account: Account): Promise<Account> {
  if (!account.token) {
    return fatalError(
      `Cannot update an account which doesn't have a token: ${account.login}`
    )
  }

  return fetchUser(account.endpoint, account.token)
}
```

**File:** app/src/ui/invalidated-token/invalidated-token.tsx (L19-43)
```typescript
export class InvalidatedToken extends React.Component<IInvalidatedTokenProps> {
  public render() {
    const { account } = this.props

    return (
      <Dialog
        id="invalidated-token"
        type="warning"
        title={
          __DARWIN__ ? 'Invalidated Account Token' : 'Invalidated account token'
        }
        onSubmit={this.onSubmit}
        onDismissed={this.props.onDismissed}
      >
        <DialogContent>
          Your account token has been invalidated and you have been signed out
          from your <Ref>{account.friendlyEndpoint}</Ref> account. Do you want
          to sign in again?
        </DialogContent>
        <DialogFooter>
          <OkCancelButtonGroup okButtonText="Yes" cancelButtonText="No" />
        </DialogFooter>
      </Dialog>
    )
  }
```
