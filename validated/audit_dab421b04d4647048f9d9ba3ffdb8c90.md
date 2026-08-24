## Analysis Result

### Title
Non-deterministic host classification lets `eraseCredential()` silently skip revocation of a previously stored generic Git credential - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The Solidity report's broken invariant is: a "revoke" code path re-derives its own criteria (empty `_rights`) instead of reusing the exact criteria the "grant" path used, so the revoke silently no-ops on a `DelegateRegistry` key that was never actually the one holding the delegation, leaving stale attacker-usable state. GitHub Desktop's generic Git credential helper has the same structural flaw: `storeCredential()` and `eraseCredential()` both gate their action behind an independently-recomputed, attacker-influenceable classification (`getEndpointKind()`), rather than persisting/reusing the classification decided when the credential was stored. If the classification differs between the `store` and `erase` invocations for the same endpoint, the erase call becomes a no-op and the previously stored secret is never removed from the OS keychain/localStorage.

### Finding Description
`storeCredential()` and `eraseCredential()` both call `getEndpointKind(cred, store)` and only touch the generic credential store (`setGenericCredential` / `deleteGenericCredential`) when the result is `'generic'`: [1](#0-0) 

`getEndpointKind()` is not a pure function of the endpoint alone — it depends on transient, network/attacker-controlled signals: `WWW-Authenticate` header realms echoed by the remote server, whether an account has since been added for that endpoint via `findGitHubTrampolineAccount`, and a live network probe `isGitHubHost(endpoint)`: [2](#0-1) 

Because a malicious or compromised remote host (or a MITM-capable proxy sitting on that remote) controls the `wwwauth[...]` headers Git forwards to the helper, and controls the response used by `isGitHubHost()`, the same physical endpoint can be classified `'generic'` at credential-store time (right after a successful login, when Git calls `store`) and classified `'enterprise'`/`'github.com'` at credential-erase time (when Git later calls `erase` after a failed re-auth). When that happens, `eraseCredential()`'s `!== 'generic'` guard causes it to return immediately without ever calling `deleteGenericCredential()`, so the username/password (or PAT) written earlier by `setGenericCredential()`/`TokenStore.setItem` remains in the OS keychain and the associated username remains in `localStorage`: [3](#0-2) 

This is structurally identical to the `revokeDelegate()` flaw: the removal path recomputes a key/condition independently from the one used at grant time, so a state that was correctly written under one set of criteria is never actually cleared when the criteria drift, and stale credential material persists and remains retrievable by `findGenericTrampolineAccount()` for any subsequent Git operation against that same host: [4](#0-3) 

### Impact Explanation
A stale, "supposedly revoked" username/password or personal access token for a Git host remains resident in the OS-level secure storage (keychain/Credential Manager/libsecret) and in `localStorage`, and continues to be handed back to Git by `getGenericCredential()`/`findGenericTrampolineAccount()` for future operations against that host. This is a silent credential-retention bug: the user or the app believed the credential was erased (Git explicitly asked to `erase` it after an auth failure, normally because the credential was rejected/rotated), yet Desktop keeps using/offering the old secret. If the old credential belonged to a different identity than the one currently expected (e.g. after credential rotation, or a shared machine/account transition), operations can silently authenticate as the stale identity, and the secret remains exfiltratable from disk/keychain long after the user thought it was removed.

### Likelihood Explanation
The attacker only needs to control the remote server's HTTP responses (the `WWW-Authenticate` header value, or the response consumed by `isGitHubHost()`) across two Git invocations against the same host — no local access, no admin rights, and no unnatural user action are required, since `store`/`erase` credential-helper calls are triggered automatically by ordinary `git fetch`/`push` authentication retries against a repository/remote the attacker controls.

### Recommendation
Persist the classification decision (`'generic'` vs `'github.com'`/`'enterprise'`) made at `store` time alongside the stored credential (or key the generic store off of that decision) and reuse it at `erase` time instead of recomputing `getEndpointKind()` independently. Alternatively, always attempt `deleteGenericCredential()` in `eraseCredential()` regardless of the freshly-computed endpoint kind, so revocation is unconditional and cannot be short-circuited by a change in the remote server's responses between the two calls.

### Proof of Concept
1. Attacker stands up a Git-over-HTTPS server that responds with `WWW-Authenticate: Basic realm="GitLab"` on the first authentication attempt.
2. User adds this remote in Desktop and successfully authenticates; `getEndpointKind()` returns `'generic'`, and `storeCredential()` persists the username/password via `setGenericCredential()` → `TokenStore.setItem` under `getKeyForEndpoint(endpoint)`.
3. The credential is later rotated/rejected server-side, and Git invokes the `erase` command for the same endpoint. In the interim, the attacker's server has stopped sending the `WWW-Authenticate` realm header (or the user has separately configured a GitHub Enterprise account whose endpoint resolves to the same origin), so `getEndpointKind()` now returns `'enterprise'`/`'github.com'`.
4. `eraseCredential()`'s `!== 'generic'` check causes it to return without calling `deleteGenericCredential()`; the old username/password remains in the OS keychain and `localStorage` indefinitely, and is still returned by `findGenericTrampolineAccount()`/`getGenericCredential()` for subsequent Git operations against that host.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-179)
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
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L181-213)
```typescript
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

**File:** app/src/lib/generic-git-auth.ts (L22-48)
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

/** Get the password for the given username and host. */
export const getGenericPassword = (endpoint: string, username: string) =>
  TokenStore.getItem(getKeyForEndpoint(endpoint), username)

/** Delete a generic credential */
export function deleteGenericCredential(endpoint: string, username: string) {
  localStorage.removeItem(getKeyForUsername(endpoint))
  return TokenStore.deleteItem(getKeyForEndpoint(endpoint), username)
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
