### Title
Removing a GitHub/Enterprise account does not clear stale generic-git credentials, allowing continued authenticated Git operations after "sign out" - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The vulnerability class in the report is: a single underlying asset (staking token) is tracked by two independent accounting systems (`StakingRewards.sol`/`MasterChef.sol` and `ConvexStakingWrapper.sol`), and an emergency "shelter" action wipes state in only one of them, letting the attacker keep drawing rewards from the un-wiped system. The GitHub Desktop analog is that Git credentials for a given remote endpoint can be tracked by two independent stores — the GitHub account token store (`AccountsStore`/`TokenStore`/keychain) and the generic Git credential store (`generic-git-auth.ts`, also backed by keychain) — and "signing out" (`_removeAccount`) wipes only the first, leaving the second intact and usable.

### Finding Description
When a user signs in to GitHub Desktop, the account token is stored in the OS keychain under key `getKeyForAccount(account)` = `getKeyForEndpoint(account.endpoint)` with the account login as the keychain "account" field [1](#0-0) .

Independently, the trampoline credential helper can also store *generic* Git credentials (arbitrary username/password) for the same endpoint via `setGenericCredential`, which persists to the **same keychain service key** (`getKeyForEndpoint(endpoint)`) but under a different username entry [2](#0-1) . This happens whenever Git prompts for credentials for a host that the credential helper classifies as `'generic'` (e.g., a self-hosted GHE Server instance that the helper cannot positively identify as GitHub, or any custom host) — see `storeCredential` / `getEndpointKind` [3](#0-2) .

`isGHES` classifies **any endpoint that isn't dotcom and isn't `*.ghe.com`** as a GitHub Enterprise Server candidate [4](#0-3) , and for such hosts `getEndpointKind` falls back to a live `isGitHubHost(endpoint)` network probe (or `wwwauth` header sniffing) to decide `'enterprise'` vs `'generic'` [5](#0-4) . If that probe fails or is inconclusive (offline, proxy, corporate firewall, or the endpoint is later reachable only after removal), the endpoint is treated as `'generic'`, and Desktop will consult/store the generic-credential store for it, independent of whatever GitHub account object exists for that same endpoint.

When the user removes/signs out of an account, `AppStore._removeAccount` only:
1. Deletes the account from `AccountsStore` (keychain entry keyed by the account's `login`), and
2. Revokes the OAuth token via `deleteToken` (API call). [6](#0-5) [7](#0-6) 

Nowhere in this flow is `deleteGenericCredential` called for that endpoint. The generic-credential keychain entry (a separate username/password pair for the same host, stored via `setGenericCredential`) is never wiped [8](#0-7) .

In `getCredential`, the lookup order is: (1) `getGitHubCredential` via `findGitHubTrampolineAccount` (now empty post-removal), then (2) `getEndpointKind` — if it resolves to `'generic'` for this host, (3) `getGenericCredential`/`findGenericTrampolineAccount`, which reads directly from the untouched generic keychain entry and returns it as valid credentials, with no reference to whether an account was ever signed out [9](#0-8) [10](#0-9) .

This mirrors the reported bug precisely: the "shelter"/sign-out action (`_removeAccount`) only wipes one of two independent stores that both track credentials/authorization for the same identity/endpoint, so the un-wiped store continues to grant access ("rewards") the user believed had been revoked.

### Impact Explanation
An attacker who can plant/poison a generic credential for a host the victim also uses a GitHub/GHE account on (e.g., via a malicious cloned repo's remote prompting Git for creds, a crafted `.gitconfig`, or a credential the user typed once for that host before adding the OAuth account) can retain silent, persistent authenticated Git access to that endpoint after the victim believes they have signed out. Since Desktop treats the generic credential as fully valid for fetch/push, this can lead to unauthorized repository access/push using stale credentials the user thought were revoked — undermining the security guarantee of "Sign Out."

### Likelihood Explanation
Requires: (a) the endpoint being classified as `'generic'` by `getEndpointKind` at some point (realistic for GHES instances behind proxies/firewalls where `isGitHubHost` probing is unreliable, or non-`.ghe.com`/non-dotcom hosts), and (b) a generic credential having been previously stored for that same endpoint (via a normal Git credential prompt) alongside a GitHub account. This is a plausible but non-default combination of app states, consistent with the Medium severity the original finding received (contingent on a specific pre-condition, limited additional-impact scope).

### Recommendation
When removing an account (`AppStore._removeAccount`/`AccountsStore.removeAccount`), also call `deleteGenericCredential` for the account's endpoint (and any host aliases, e.g. HTML URL vs API URL) to ensure no fallback credential store retains access for that identity. Alternatively, make `getEndpointKind`'s classification sticky/authoritative once an account has ever been associated with that endpoint, so a transient network failure in `isGitHubHost` cannot silently downgrade a known GitHub endpoint to `'generic'` and unlock the stale credential path.

### Proof of Concept
1. Add a GitHub Enterprise Server account in Desktop for `https://git.internal.example.com` (an instance not under `*.ghe.com`).
2. At some point, cause Git to also store a generic credential for that same host (e.g., clone/fetch a repo whose remote briefly appears reachable only over a network path where `isGitHubHost` probing fails/times out, causing `getEndpointKind` to classify it as `'generic'` and `storeCredential` to persist a generic username/password via `setGenericCredential`) — see `storeCredential` [11](#0-10) .
3. Sign out of the GHE account in Desktop's Accounts preferences (`onLogout` → `dispatcher.removeAccount` → `AppStore._removeAccount`) [12](#0-11) [6](#0-5) .
4. Perform a `git fetch`/`push` against the same remote through Desktop's trampoline credential helper. Because `findGitHubTrampolineAccount` now returns nothing but the generic credential entry was never deleted, `getCredential` falls through to `getGenericCredential`/`findGenericTrampolineAccount`, which successfully returns the still-valid stored credential [9](#0-8) , allowing continued authenticated access despite the account having been "removed."

### Citations

**File:** app/src/lib/auth.ts (L1-13)
```typescript
import { Account } from '../models/account'

/** Get the auth key for the user. */
export function getKeyForAccount(account: Account): string {
  return getKeyForEndpoint(account.endpoint)
}

/** Get the auth key for the endpoint. */
export function getKeyForEndpoint(endpoint: string): string {
  const appName = __DEV__ ? 'GitHub Desktop Dev' : 'GitHub'

  return `${appName} - ${endpoint}`
}
```

**File:** app/src/lib/generic-git-auth.ts (L22-39)
```typescript
/** Set the password for the username and host. */
export function setGenericPassword(
  endpoint: string,
  username: string,
  password: string
): Promise<void> {
  const key = getKeyForEndpoint(endpoint)
  return TokenStore.setItem(key, username, password)
}

export function setGenericCredential(
  endpoint: string,
  username: string,
  password: string
) {
  setGenericUsername(endpoint, username)
  return setGenericPassword(endpoint, username, password)
}
```

**File:** app/src/lib/generic-git-auth.ts (L45-48)
```typescript
/** Delete a generic credential */
export function deleteGenericCredential(endpoint: string, username: string) {
  localStorage.removeItem(getKeyForUsername(endpoint))
  return TokenStore.deleteItem(getKeyForEndpoint(endpoint), username)
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-135)
```typescript
/** Implementation of the 'get' git credential helper command */
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

  // GitHub.com/GHE creds are only stored internally
  if (endpointKind !== 'generic') {
    return undefined
  }

  return useExternalCredentialHelper()
    ? getExternalCredential(cred, token)
    : getGenericCredential(cred, token)
}
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-213)
```typescript
const getEndpointKind = async (cred: Credential, store: Store) => {
  const credentialUrl = getCredentialUrl(cred)
  const endpoint = `${credentialUrl}`

  if (isGist(endpoint)) {
    return 'generic'
  }

  if (isDotCom(endpoint)) {
    return 'github.com'
  }

  if (isGHE(endpoint)) {
    return 'ghe.com'
  }

  // When Git attempts to authenticate with a host it captures any
  // WWW-Authenticate headers and forwards them to the credential helper. We
  // use them as a happy-path to determine if the host is a GitHub host without
  // having to resort to making a request ourselves.
  for (const [k, v] of cred.entries()) {
    if (k.startsWith('wwwauth[')) {
      if (v.includes('realm="GitHub"')) {
        return 'enterprise'
      } else if (/realm="(GitLab|Gitea|Atlassian Bitbucket)"/.test(v)) {
        return 'generic'
      }
    }
  }

  const existingAccount = await findGitHubTrampolineAccount(store, endpoint)
  if (existingAccount) {
    return isDotCom(existingAccount.endpoint) ? 'github.com' : 'enterprise'
  }

  // All GitHub hosts use HTTPS, so if the protocol is not HTTPS we can
  // assume that this is not a GitHub host.
  if (credentialUrl.protocol !== 'https:') {
    return 'generic'
  }

  return (await isGitHubHost(endpoint)) ? 'enterprise' : 'generic'
}

/** Implementation of the 'store' git credential helper command */
async function storeCredential(cred: Credential, store: Store, token: string) {
  if ((await getEndpointKind(cred, store)) !== 'generic') {
    return
  }

  return useExternalCredentialHelper()
    ? storeExternalCredential(cred, token)
    : setGenericCredential(
        urlWithoutCredentials(getCredentialUrl(cred)),
        forceUnwrap(`credential missing username`, cred.get('username')),
        forceUnwrap(`credential missing password`, cred.get('password'))
      )
}

const storeExternalCredential = (cred: Credential, token: string) => {
  const path = getTrampolineEnvironmentPath(token)
  return approveCredential(cred, path, getGcmEnv(token))
}

/** Implementation of the 'erase' git credential helper command */
async function eraseCredential(cred: Credential, store: Store, token: string) {
  if ((await getEndpointKind(cred, store)) !== 'generic') {
    return
  }

  return useExternalCredentialHelper()
    ? eraseExternalCredential(cred, token)
    : deleteGenericCredential(
        urlWithoutCredentials(getCredentialUrl(cred)),
        forceUnwrap(`credential missing username`, cred.get('username'))
      )
}
```

**File:** app/src/lib/endpoint-capabilities.ts (L61-68)
```typescript
/** Whether or not the given endpoint URI is under the ghe.com domain */
export const isGHE = (ep: string) => new URL(ep).hostname.endsWith('.ghe.com')

/**
 * Whether or not the given endpoint URI appears to point to a GitHub Enterprise
 * Server instance
 */
export const isGHES = (ep: string) => !isDotCom(ep) && !isGHE(ep)
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

**File:** app/src/lib/trampoline/find-account.ts (L31-59)
```typescript
export async function findGenericTrampolineAccount(
  trampolineToken: string,
  remoteUrl: string
) {
  const parsedUrl = new URL(remoteUrl)
  const endpoint = urlWithoutCredentials(remoteUrl)

  const login =
    parsedUrl.username === ''
      ? getGenericUsername(endpoint)
      : parsedUrl.username

  if (!login) {
    return undefined
  }

  const token = await memoizedGetGenericPassword(
    trampolineToken,
    endpoint,
    login
  )

  if (!token) {
    // We have a username but no password, that warrants a warning
    log.warn(`credential: generic password for ${remoteUrl} missing`)
    return undefined
  }

  return { login, endpoint, token }
```

**File:** app/src/ui/preferences/preferences.tsx (L498-501)
```typescript
  private onLogout = (account: Account) => {
    this.props.dispatcher.removeAccount(account)
  }

```
