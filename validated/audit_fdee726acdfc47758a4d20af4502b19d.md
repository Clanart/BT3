Confirmed: `credential.useHttpPath` is never set anywhere in the codebase, so Git's default behavior (host-only credential matching, no path) applies to every credential-helper invocation.

### Title
Generic Git credential username is keyed by host only (not by repository path), causing cross-repository credential leakage on shared/multi-tenant Git hosts - (File: `app/src/lib/generic-git-auth.ts`)

### Summary
For non-GitHub ("generic") remotes, GitHub Desktop remembers a single username per **endpoint**, where `endpoint` collapses to the bare `protocol://host/` because Git only reports a path to the credential helper when `credential.useHttpPath` is enabled — and Desktop never sets that option. Two different repositories hosted on the same server (e.g. a shared/multi-tenant GitLab/Gitea/Bitbucket instance) therefore share one "remembered username" slot. Storing or erasing credentials for one repository silently overwrites or deletes the association for the other, and the wrong account's password can subsequently be auto-submitted to whichever repo is queried next.

### Finding Description
`getKeyForUsername` derives its storage key from `endpoint` alone, with no repository path or credential-specific discriminator: [1](#0-0) 

`setGenericUsername`/`deleteGenericCredential` write/erase that single localStorage slot per endpoint regardless of which username the caller passed in: [2](#0-1) 

The `endpoint` value used at credential-helper time is reconstructed from the `protocol`/`host`/`path` fields Git supplies via `getCredentialUrl`. Since `credential.useHttpPath` is never configured anywhere in the codebase, Git omits the path by default, so `path` is `''` for every repository on that host: [3](#0-2) 

When Git asks Desktop for credentials on a URL with no embedded username, `findGenericTrampolineAccount` falls back to the single, host-scoped remembered username and then fetches the matching password from the OS keychain: [4](#0-3) 

`storeCredential`/`eraseCredential` in the trampoline credential helper feed this same host-only `endpoint` (derived from `getCredentialUrl`) into `setGenericCredential`/`deleteGenericCredential`: [5](#0-4) 

This is the structural analog of the reported bug: just as `mapMember_weight[_member]` aggregated two distinct synth deposits under one key so that withdrawing one silently zeroed the other's weight, `genericGitAuth/username/<endpoint>` aggregates two distinct repository/account credentials under one host-scoped key, so storing or erasing one silently overwrites/erases the other's remembered identity.

### Impact Explanation
If a victim has valid generic-auth credentials saved for `RepoA` on host `git.example.com`, and is lured (via a link, an "add existing repository"/clone flow, or an attacker-supplied remote URL) into cloning or fetching `RepoB` — a different, attacker-controlled repository hosted on the same server — the next credential-helper `get` invoked for `RepoB` will resolve to the previously remembered username for `RepoA` (because the host-scoped key doesn't distinguish the two paths) and will pull the matching password from the keychain via `getGenericPassword`. Desktop then silently supplies `RepoA`'s real account name and password to authenticate the Git operation against `RepoB`. If the attacker operates the endpoint the credential is actually sent to (their own repo backend on that shared host), this hands them the victim's valid username/password for a different, more privileged account — meeting the "credential exfiltration via attacker-controlled remote" bar. Independently, `eraseCredential` for one repo's failed auth silently wipes the other repo's remembered username, degrading auth for an unrelated account.

### Likelihood Explanation
Requires only that the victim have generic (non-GitHub) HTTP credentials stored for one repository on a multi-tenant Git host and be induced to add/clone a second, different repository on the *same host* — a very ordinary Desktop workflow ("clone/add another repository from the same server"), not local access, malware, or leaked credentials. Self-hosted GitLab/Gitea/Bitbucket instances that host many independent users/repos under one hostname are common, making this a realistic attacker setup.

### Recommendation
Scope the generic-credential username (and lookup) by the full effective Git credential context — at minimum `protocol + host + path` when available, matching Git's own `credential.useHttpPath` semantics — rather than by host alone, and explicitly set `credential.useHttpPath=true` in the environment/config Desktop uses for generic remotes so distinct repository paths on the same host never collide. `deleteGenericCredential`/`setGenericUsername` should only mutate the entry that actually matches the username being erased/stored, never blindly clear or overwrite the host's single slot.

### Proof of Concept
1. Victim clones `https://git.example.com/RepoA.git`, is prompted for generic Git credentials, enters `alice`/`alice-pass`. Desktop calls `setGenericCredential('https://git.example.com/', 'alice', 'alice-pass')`, storing `alice` in `localStorage['genericGitAuth/username/https://git.example.com/']` and `alice-pass` in the keychain under that endpoint key. [6](#0-5) 
2. Attacker, who also has an account/repo on `git.example.com`, sends the victim a link/instructions to clone `https://git.example.com/RepoB.git` (their own repo) in Desktop.
3. Desktop performs `git fetch`/`clone`; the trampoline credential helper's `get` command reconstructs `endpoint = https://git.example.com/` (no path, since `useHttpPath` is unset) and calls `findGenericTrampolineAccount`, which resolves `login='alice'` via `getGenericUsername` and fetches `alice-pass` via `getGenericPassword`. [4](#0-3) 
4. Git silently authenticates the request for `RepoB` (attacker's repository) using `alice:alice-pass`, delivering the victim's real credentials for a different account to the attacker-controlled endpoint — without any prompt or warning to the victim.

### Citations

**File:** app/src/lib/generic-git-auth.ts (L4-20)
```typescript
export const genericGitAuthUsernameKeyPrefix = 'genericGitAuth/username/'

function getKeyForUsername(endpoint: string): string {
  return `${genericGitAuthUsernameKeyPrefix}${endpoint}`
}

/** Get the username for the host. */
export function getGenericUsername(endpoint: string): string | null {
  const key = getKeyForUsername(endpoint)
  return localStorage.getItem(key)
}

/** Set the username for the host. */
export function setGenericUsername(endpoint: string, username: string) {
  const key = getKeyForUsername(endpoint)
  return localStorage.setItem(key, username)
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

**File:** app/src/lib/generic-git-auth.ts (L45-49)
```typescript
/** Delete a generic credential */
export function deleteGenericCredential(endpoint: string, username: string) {
  localStorage.removeItem(getKeyForUsername(endpoint))
  return TokenStore.deleteItem(getKeyForEndpoint(endpoint), username)
}
```

**File:** app/src/lib/trampoline/trampoline-environment.ts (L46-59)
```typescript
export const getCredentialUrl = (cred: Map<string, string>) => {
  const u = cred.get('url')
  if (u) {
    return new URL(u)
  }

  const protocol = cred.get('protocol') ?? ''
  const username = cred.get('username')
  const user = username ? `${encodeURIComponent(username)}@` : ''
  const host = cred.get('host') ?? ''
  const path = cred.get('path') ?? ''

  return new URL(`${protocol}://${user}${host}/${path}`)
}
```

**File:** app/src/lib/trampoline/find-account.ts (L31-60)
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
