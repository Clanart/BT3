Confirmed: this is the closest and best-supported analog to the PoolTogether "old yield source still has infinite approval" pattern.

### Title
Sign-out revokes local account state before confirming remote OAuth token revocation, leaving GitHub-side token permanently valid on API failure - (File: `app/src/lib/stores/app-store.ts`)

### Summary
When a user "swaps" (signs out / removes) a GitHub.com or GitHub Enterprise account in Desktop, the app deletes the account and its cached token from local secure storage first, then fires a best-effort request to GitHub's `DELETE applications/:client_id/token` endpoint to actually revoke the OAuth token server-side. If that revocation request fails for any reason, the failure is silently swallowed and logged only — the app never retries, never surfaces the failure to the user, and unconditionally reports the account as "signed out." This mirrors the report's root cause: the "old" credential object (the OAuth token) keeps its full, unrestricted approval/validity after the app has moved on to a new trust state, because the deactivation step is not enforced as a precondition of the swap.

### Finding Description
`_removeAccount` performs the two steps in sequence without any dependency between them: [1](#0-0) 

`accountsStore.removeAccount` deletes the token from the OS keychain/secure store and drops the account from Desktop's in-memory/persisted account list: [2](#0-1) 

Only after that has already completed does Desktop call `deleteToken(account)`, which performs the actual server-side revocation: [3](#0-2) 

`deleteToken` wraps the whole call in a try/catch that returns `false` on *any* error — expired session, network failure, a GHE endpoint that is unreachable, rate-limited, or returns an unexpected status — and the caller (`_removeAccount`) does not check this return value at all. The UI (`Accounts` component, `onLogout`/`logout`) has no branch for revocation failure; the account simply disappears from the list as if sign-out fully succeeded: [4](#0-3) 

The broken invariant is: *"an account shown as signed-out in Desktop implies its OAuth token is no longer valid on GitHub."* That invariant does not hold — Desktop only guarantees the token is gone from its own local store, not that the grant has been revoked upstream. If the token was already exfiltrated before sign-out (e.g., via a malicious `.git/config`-triggered credential leak to a hostile endpoint, a compromised proxy sitting in front of a GHE `endpoint`, or any other channel that captured `account.token`), the attacker's copy of the token remains fully authorized indefinitely, because Desktop's own removal flow does not confirm/enforce revocation and gives the user false assurance that it "signed out."

Existing guards do not stop this path: there is no retry, no error propagation via `emitError`, and no persisted "pending revocation" state that could be retried on next launch — once `_removeAccount` returns, Desktop considers the matter closed regardless of whether `deleteToken` succeeded.

### Impact Explanation
If a token was previously exposed to an attacker (e.g., through a hostile git remote/proxy response abusing the credential-helper trampoline, or a compromised GHE endpoint that also happens to be the same endpoint targeted by `deleteToken`), the user's attempt to remediate by signing out of Desktop provides **no actual security boundary** when the revocation call fails — silently and invisibly to the user. The attacker retains full API/account access with the stale token for as long as GitHub's normal token lifetime allows, while the victim believes the credential has been cut off. This is a credential/token exfiltration-and-persistence issue matching the "unauthorized OAuth" impact category.

### Likelihood Explanation
Likelihood is moderate: it requires (a) a prior token disclosure via some other channel and (b) the revocation call to fail at the moment of sign-out (network blip, GHE instance down/unreachable, endpoint under attacker control acting as a man-in-the-middle/proxy that simply drops or 5xxs the DELETE request). Because GHE endpoints are user-specified arbitrary hosts, an attacker who controls or intercepts traffic to that endpoint can reliably force `deleteToken` to fail while letting all other UI flows appear to succeed, since Desktop performs no verification step.

### Recommendation
Do not remove the local account/token before the server-side revocation is confirmed. Reorder `_removeAccount` to call `deleteToken` first and check its boolean result; only clear local state on success. On failure, surface the error to the user via `emitError`/a popup explicitly stating that sign-out did not fully complete and the token is still active, and retry automatically (with backoff) or on next app start, similar to how `onTokenInvalidated` treats token/account mismatches as an error condition rather than a no-op.

### Proof of Concept
1. Sign in to a GitHub Enterprise account whose `endpoint` is attacker-influenceable/interceptable (e.g., a GHE server behind a proxy the attacker can transiently disrupt, or simulate by blocking outbound requests to that endpoint at the network layer for the test).
2. Exfiltrate the token value beforehand (for the PoC, simply capture `account.token` from the keychain to simulate a prior leak via any other channel, e.g. the generic-git-auth/trampoline credential flow).
3. In Desktop, go to Preferences → Accounts → "Sign out" for that account while the endpoint is unreachable/blocked, causing `deleteToken` in `app/src/lib/api.ts` (lines 2214-2231) to throw/return `false`.
4. Observe: `app/src/lib/stores/app-store.ts` `_removeAccount` (lines 8023-8029) completes, `accountsStore.removeAccount` has already deleted the local keychain entry, the account disappears from the Accounts UI with no error shown, and no retry ever occurs.
5. Using the token captured in step 2, issue an authenticated request to the GitHub API — it still succeeds because the DELETE to `applications/{client_id}/token` never reached GitHub, proving the "old" grant retains full, unrevoked access after the local "swap."

### Citations

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

**File:** app/src/lib/stores/accounts-store.ts (L161-180)
```typescript
  public async removeAccount(account: Account): Promise<void> {
    await this.loadingPromise

    try {
      await this.secureStore.deleteItem(
        getKeyForAccount(account),
        account.login
      )
    } catch (e) {
      log.error(`Error removing account '${account.login}'`, e)
      this.emitError(e)
      return
    }

    this.accounts = this.accounts.filter(
      a => !(a.endpoint === account.endpoint && a.id === account.id)
    )

    this.save()
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

**File:** app/src/ui/preferences/accounts.tsx (L152-156)
```typescript
  private logout = (account: Account) => {
    return () => {
      this.props.onLogout(account)
    }
  }
```
