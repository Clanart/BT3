### Title
On-Path GHE Server/Proxy Can Force Silent Account Sign-Out and Remote Token Revocation via Spoofed 401 Header - ([File: app/src/lib/api.ts])

### Summary
The external report's broken invariant is "a failed sensitive operation must trigger cleanup (deletion) of the associated account/credential." The closest Desktop analog is inverted but structurally identical: Desktop treats an **unauthenticated response header from the remote server** as sufficient proof that a token is invalid, and on that basis it performs the destructive cleanup — deleting the stored token and revoking it via the GitHub API — without any additional verification that the credential is truly compromised.

### Finding Description
`API.ghRequest` decides that an account's token has been invalidated purely by inspecting response status/headers returned by the endpoint the account talks to: [1](#0-0) 

The only checks are: HTTP 401, presence of `X-GitHub-Request-Id`, and absence of `X-GitHub-OTP`. All three of these are ordinary HTTP response headers that any server (or any on-path component terminating/proxying the connection, such as a compromised or malicious GitHub Enterprise instance, or a captive/corporate proxy in front of it) can freely set on any response, including a 401 with no real relationship to OAuth token validity.

Once `API.emitTokenInvalidated(this.endpoint, this.token)` fires, `AppStore.onTokenInvalidated` immediately signs the account out and destroys its credentials: [2](#0-1) 

`_removeAccount` then deletes the token from the local secure store and also calls the GitHub API to **revoke the token server-side**: [3](#0-2) [4](#0-3) 

There is no re-validation step (e.g., re-querying `/user` on the canonical endpoint, or requiring the same failure to be observed across multiple independent requests) before this irreversible action is taken. The guard added to filter out 2FA-required responses (`!response.headers.has('X-GitHub-OTP')`) only narrows one false-positive case; it does nothing to authenticate that the 401 and its headers actually originated from GitHub's real authorization service rather than from an attacker-controlled endpoint/proxy the request happened to be routed through (this is explicitly true for GitHub Enterprise accounts, where `account.endpoint` is a self-hosted, often on-prem/behind-corporate-proxy URL).

### Impact Explanation
Any component sitting in the request path to a GHE `account.endpoint` (a compromised GHE appliance, a malicious/misconfigured reverse proxy, or a rogue instance a user was pointed at) can, without needing valid credentials or the real token, cause GitHub Desktop to:
1. Delete the user's stored OAuth/PAT credential from the OS keychain, and
2. Actively call GitHub's OAuth Apps API to revoke that token for real, cutting the user's access on the legitimate GitHub Enterprise account (`deleteToken`).

This is a persistent, silent account-unbinding/denial-of-access primitive triggered purely by a crafted response — no user interaction beyond Desktop making its normal background API calls (refresh, PR checks, etc.) is required. It matches the "unauthorized OAuth or account binding" impact class: the attacker forces the token relationship between the user and their account to be unilaterally and permanently torn down.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to control or intercept traffic to the account's endpoint (realistic for GHE deployments behind a compromised proxy/load balancer, or where the "server" itself is attacker-controlled/compromised), which is explicitly within the accepted "git remote/proxy response" threat model for this analysis. No user action beyond normal app usage (Desktop polling the API in the background) is needed once the attacker can inject a single 401 response with the two headers set as required.

### Recommendation
Do not perform destructive account actions (local deletion + remote token revocation) based solely on response headers from a single request. At minimum:
- Require corroboration, e.g. re-check via a second independent call (ideally over a pinned/validated TLS connection) before revoking.
- Do not call `deleteToken` (which actively revokes the token server-side) automatically; reserve that for explicit user-initiated sign-out, and instead only clear the local session on the (still-not-fully-trustworthy) 401 signal, prompting the user to confirm before revoking the credential remotely.
- Add rate limiting / repetition requirements before honoring `emitTokenInvalidated`.

### Proof of Concept
1. Add a GitHub Enterprise account in Desktop pointing at `https://ghe.internal.example`.
2. Position as, or compromise, a proxy/load balancer in front of that endpoint (or run a malicious server there).
3. When Desktop makes any authenticated background API call (e.g., periodic account refresh in `AccountsStore.refresh` → `fetchUser`), respond with:
   - Status: `401`
   - Headers: `X-GitHub-Request-Id: <any-value>` present, `X-GitHub-OTP` absent.
4. `API.ghRequest` at [5](#0-4)  calls `emitTokenInvalidated`.
5. `AppStore.onTokenInvalidated` ( [6](#0-5) ) calls `_removeAccount`, which deletes the local token and calls `deleteToken` to revoke it on GitHub's real servers, signing the user out of a legitimate account without their consent.

### Citations

**File:** app/src/lib/api.ts (L1841-1854)
```typescript
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
```

**File:** app/src/lib/api.ts (L2214-2231)
```typescript
export async function deleteToken(account: Account) {
  try {
    const creds = Buffer.from(`${ClientID}:${ClientSecret}`).toString('base64')
    const response = await request(
      account.endpoint,
      null,
      'DELETE',
      `applications/${ClientID}/token`,
      { access_token: account.token },
      { Authorization: `Basic ${creds}` }
    )

    return response.status === 204
  } catch (e) {
    log.error(`deleteToken: failed with endpoint ${account.endpoint}`, e)
    return false
  }
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

**File:** app/src/lib/stores/app-store.ts (L8023-8029)
```typescript
  public async _removeAccount(account: Account) {
    log.info(
      `[AppStore] removing account ${account.login} (${account.name}) from store`
    )
    await this.accountsStore.removeAccount(account)
    await deleteToken(account)
  }
```
