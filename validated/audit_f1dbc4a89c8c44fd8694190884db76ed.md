## Finding



### Title
Malicious git server can hijack the OS-keychain slot of a privileged GitHub/GHE account via credential-helper endpoint misclassification - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The trampoline credential helper decides whether a remote endpoint is a "GitHub" endpoint (privileged, internally-managed credentials) or "generic" (arbitrary username/password managed by `generic-git-auth.ts`) using `getEndpointKind`. That function trusts a `wwwauth[]` value that git forwards verbatim from the remote server's HTTP response, and returns `'generic'` immediately whenever that header names GitLab/Gitea/Bitbucket — **before** ever checking whether Desktop already has a signed-in GitHub/Enterprise account for that same endpoint. Because the "generic" write path stores credentials under the identical OS-keychain key used for the real account, a malicious/compromised remote (or MITM proxy in front of a GHE server the user already uses) can cause Desktop to overwrite the real account's stored token with attacker-influenced data.

### Finding Description
`getEndpointKind` in `app/src/lib/trampoline/trampoline-credential-helper.ts:137-179` classifies the credential endpoint: [1](#0-0) 

The `wwwauth[]` entries come straight from git, which itself forwards the server's `WWW-Authenticate` response headers into the credential-helper's stdin — i.e. this value is fully attacker-controlled by whatever server the git client is talking to for that remote. If the header contains `realm="GitLab"`, `realm="Gitea"`, or `realm="Atlassian Bitbucket"`, the function returns `'generic'` immediately, **skipping** the subsequent call to `findGitHubTrampolineAccount(store, endpoint)` (line 167) that would otherwise detect an existing privileged account for that endpoint.

Once classified as `'generic'`, `getCredential` (lines 94-135) bypasses the GitHub-account lookup/sign-in path entirely and falls to `getGenericCredential`/`promptForCredential`, and `storeCredential` (lines 182-194) will persist whatever the user types via: [2](#0-1) 

`setGenericPassword`/`setGenericCredential` write to `TokenStore.setItem(getKeyForEndpoint(endpoint), username, password)`, and `getKeyForEndpoint` builds the OS keychain service key as: [3](#0-2) 

This is the **exact same key** used by `AccountsStore` to store the real, privileged GitHub/GHE account token: [4](#0-3) 

`TokenStore.setItem` calls `keytar.setPassword(service, account, password)` directly, with no existence check: [5](#0-4) 

Keytar's `setPassword` silently **overwrites** any existing secret for the same `(service, account)` pair. So if the credential endpoint string equals the stored account's `endpoint` and the username the user enters (or that a compromised external credential helper approves) equals `account.login`, `setGenericCredential` will overwrite the real account's OAuth/PAT token in the OS secure store — with no validation that the target keychain entry already belongs to a privileged, signed-in account. This is structurally identical to `setClientOwner` in the seed report: an unprivileged identity (here, an attacker-influenced "generic" credential write) can silently reassign/overwrite the storage slot of a privileged identity (a signed-in GitHub/GHE account) because the ownership/target check is missing.

On next load, `AccountsStore.loadFromStore` reads back whatever is in that keychain slot as the account's token: [6](#0-5) 

### Impact Explanation
If exploited, the user's signed-in GitHub.com/GHE account token stored in the OS keychain (Keychain/Credential Manager/libsecret) is silently corrupted or replaced. Practical consequences:
- The account is silently signed-out/broken (all subsequent GitHub API calls with that account fail authentication), a data-integrity/availability impact on the user's authenticated session.
- If the attacker can predict or observe the value later re-approved by `store` (e.g. via an external credential helper flow, or if the user is tricked into re-entering their real PAT into what they believe is Desktop's normal GitHub prompt but is actually the "generic" prompt), the account's privileged token slot ends up holding attacker-supplied or attacker-observable material, effectively hijacking the identity binding between the app and the account for that endpoint.
- This requires no local access, no malware, and no leaked credentials up front — only that the user has a GHE/GitHub account configured in Desktop and interacts with a remote controlled by, or proxied through, an attacker (a classic supply-chain/MITM-on-clone scenario explicitly in scope).

### Likelihood Explanation
Any git server (or a network intermediary in front of one) that the user adds as a remote/clone source can freely control the `WWW-Authenticate` header returned during HTTP auth challenges. This requires no special positioning beyond "attacker controls the git remote/proxy" — an explicitly in-scope threat in this analysis. The classification bug is a straightforward one-line-condition short-circuit (`realm="GitLab|Gitea|Atlassian Bitbucket"` check happens before the existing-account lookup), making it deterministic and reliable to trigger, not probabilistic.

### Recommendation
- In `getEndpointKind`, only trust the `wwwauth[]`-based generic classification for hosts that are not already associated with a known, privileged Desktop account; check `findGitHubTrampolineAccount` (or the underlying accounts store) **before** trusting attacker-controlled `WWW-Authenticate` realm hints, not after.
- In `setGenericCredential`/`TokenStore.setItem`, refuse (or warn and require explicit confirmation) before writing a generic credential to a keychain key (`getKeyForEndpoint(endpoint)`) that is already used by a signed-in Account, mirroring the seed report's recommendation to prevent overwriting an already-associated identity slot.
- Add a regression/unit test asserting that a forged `wwwauth[]` header cannot cause the credential helper to bypass detection of, or overwrite storage for, an endpoint with an existing signed-in account.

### Proof of Concept
1. Add a remote pointing at an attacker-controlled or MITM-proxied HTTPS git server whose hostname corresponds to a GHE endpoint the user already has a signed-in Desktop account for (e.g. proxy in front of `github.example.com`).
2. Have the server respond to the git HTTP auth challenge with `WWW-Authenticate: Basic realm="GitLab"`.
3. Perform a `fetch`/`push` through Desktop against that remote. `getEndpointKind` (`app/src/lib/trampoline/trampoline-credential-helper.ts:157-165`) classifies the endpoint as `'generic'` without checking the existing account.
4. Desktop shows the generic-auth prompt (`promptForCredential`) instead of silently using the stored GHE account token; the user, believing this is the normal GitHub prompt, enters their GitHub username/PAT.
5. `storeCredential` → `setGenericCredential(endpoint, username, password)` writes via `TokenStore.setItem(getKeyForEndpoint(endpoint), username, password)` (`app/src/lib/generic-git-auth.ts:32-39`, `app/src/lib/auth.ts:8-13`) into the same keychain slot (`"GitHub - <endpoint>"` / `"GitHub Desktop - <endpoint>"`) used by `AccountsStore` for the real account (`app/src/lib/stores/accounts-store.ts:99-101`), overwriting it.
6. Restart Desktop; `AccountsStore.loadFromStore` (`app/src/lib/stores/accounts-store.ts:231-234`) reads back the corrupted token for that account, demonstrating the overwrite of the privileged account's credential slot by an attacker-influenced generic-credential write path.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L153-166)
```typescript
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

**File:** app/src/lib/stores/accounts-store.ts (L95-101)
```typescript
  public async addAccount(account: Account): Promise<Account | null> {
    await this.loadingPromise

    try {
      const key = getKeyForAccount(account)
      await this.secureStore.setItem(key, account.login, account.token)
    } catch (e) {
```

**File:** app/src/lib/stores/accounts-store.ts (L231-234)
```typescript
      const key = getKeyForAccount(accountWithoutToken)
      try {
        const token = await this.secureStore.getItem(key, account.login)
        accountsWithTokens.push(accountWithoutToken.withToken(token || ''))
```

**File:** app/src/lib/stores/token-store.ts (L1-9)
```typescript
import * as keytar from 'keytar'

function setItem(key: string, login: string, value: string) {
  return keytar.setPassword(key, login, value)
}

function getItem(key: string, login: string) {
  return keytar.getPassword(key, login)
}
```
