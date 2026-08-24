## Analysis: Silent Remote-URL Repointing via Attacker-Controlled GitHub API Response

The Solidity report's core pattern — a validation check that is *structurally incomplete* (it verifies one property but not the property that actually matters, letting a supposedly-safe operation silently break/redirect a downstream one) — has a real analog in Desktop's remote-URL auto-update logic.

### Title
Local Git remote silently repointed to an attacker-controlled host via unvalidated GitHub API `clone_url` - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
When Desktop refreshes GitHub repository metadata, it compares the API-reported `clone_url` against the local `origin` remote and, under certain conditions, calls `setRemoteURL` to silently rewrite the local git remote to match the API value. The guard only checks that the **URL scheme** (`https:` vs `ssh:`) is unchanged — it never checks that the **hostname** is unchanged. A malicious or compromised GitHub Enterprise Server (or any account object returned via the `IAPIRepository`/`apiRepo` structure) can therefore cause Desktop to silently repoint a user's `origin` remote to an entirely different host with the same protocol, without any user confirmation.

### Finding Description
`updateRemoteUrl` in [1](#0-0)  computes:
- `urlsMatch = urlMatchesRemote(updatedRemoteUrl, gitStore.defaultRemote)` — compares hostname/owner/name.
- `protocolsMatch` — compares **only** `URL.parse(...).protocol` for the current remote URL vs. the updated (API) URL.
- `remoteUrlUnchanged` — verifies the user hasn't manually edited the remote away from the last-known GitHub `cloneURL`.

The decision to call `gitStore.setRemoteURL(...)` is gated by: [2](#0-1) 

Because `protocolsMatch` only compares the scheme string (`https:` == `https:`), and `urlMatchesRemote`/`urlsMatch` is explicitly expected to be `false` (that's the trigger condition for "the repo moved"), there is **no check that the hostname of `updatedRemoteUrl` is the same as, or a legitimate variant of, the original remote's hostname**. `apiRepo.clone_url` originates from a GitHub API response — for GitHub Enterprise Server users this is a value returned by a server the user has authenticated against, which is one of the explicitly in-scope attacker primitives ("a GitHub API object"). If that API endpoint is compromised or malicious, it can return a `clone_url` such as `https://attacker.example.com/evil/evil.git`, which passes `protocolsMatch` (still `https:`) and is accepted since `!urlsMatch` is true by construction.

`setRemoteURL` then unconditionally runs `git remote set-url` with no further validation: [3](#0-2) .

This is called from the app store's repository refresh path when reconciling GitHub metadata, per the references in [4](#0-3) , i.e., this runs automatically in the background, not as a user-initiated, confirmed action.

### Impact Explanation
Once the remote is silently repointed, all subsequent `git push`/`git fetch`/`git pull` operations for that repository target the attacker's server (unless the user notices in Repository Settings). This constitutes silent corruption of where the user's commits are pushed — an explicitly valid impact — and can also lead to credential/token exposure if a credential helper sends host-scoped credentials to the new remote host, or to the user unknowingly fetching attacker-controlled objects on their next `fetch`/`pull`.

### Likelihood Explanation
This requires the user to have an authenticated relationship with a compromised/malicious GitHub Enterprise Server (or a MITM'd GitHub API response), which is within the stated attacker model ("a GitHub API object"). No local access, admin rights, or unnatural user steps are needed — the repointing happens automatically during a routine repository refresh, which occurs periodically and after sign-in. The only mitigating factor is that this specifically requires control over the metadata API rather than the git transport itself, which somewhat narrows the practical attack surface compared to a pure client-side bug, and is why this should be validated against real-world exploitability before treating it as confirmed-exploitable.

### Recommendation
In `updateRemoteUrl`, in addition to comparing protocol, validate that the hostname of `updatedRemoteUrl` matches the hostname of the existing remote (or the known GitHub/GHES endpoint associated with the account), and refuse to silently rewrite the remote if the hostname differs — instead surface a prompt asking the user to confirm the change, similar to how `enterprise-validate-url.ts` enforces `https:`-only enterprise addresses at [5](#0-4) .

### Proof of Concept
1. User has an existing repository with `origin` = `https://ghe.company.com/org/repo.git`, associated with a GitHub Enterprise account.
2. The GHE instance (compromised, or an attacker with control of a self-hosted/mirrored GHE-compatible API) responds to the repository-info API call with `clone_url: "https://attacker.example.com/org/repo.git"`.
3. Desktop's periodic repository refresh calls `updateRemoteUrl` ( [6](#0-5) ): `protocolsMatch` is `true` (`https:` == `https:`), `remoteUrlUnchanged` is `true` (user never manually edited it), `urlsMatch` is `false` (different host/owner/name) → condition at line 42 is satisfied.
4. `gitStore.setRemoteURL('origin', 'https://attacker.example.com/org/repo.git')` executes silently, rewriting `.git/config`.
5. The user's next `git push` sends their commits to `attacker.example.com` without any warning dialog.

### Citations

**File:** app/src/lib/stores/updates/update-remote-url.ts (L7-34)
```typescript
export async function updateRemoteUrl(
  gitStore: GitStore,
  gitHubRepository: GitHubRepository,
  apiRepo: IAPIRepository
): Promise<void> {
  // I'm not sure when these early exit conditions would be met. But when they are
  // we don't have enough information to continue so exit early!
  if (gitStore.defaultRemote === null) {
    return
  }

  const remoteUrl = gitStore.defaultRemote.url
  const updatedRemoteUrl = apiRepo.clone_url
  const urlsMatch = urlMatchesRemote(updatedRemoteUrl, gitStore.defaultRemote)

  // Verify that protocol hasn't changed. If it has we don't want
  // to alter the protocol in case they are relying on a specific one.
  // If protocol is null that implies the url is a ssh url
  // of the format git@github.com:octocat/Hello-World.git, which
  // can't be parsed by URL.parse. In this case we assume the user
  // manually configured their remote to use this format and we don't
  // want to change what they've done just to be safe
  const parsedRemoteUrl = URL.parse(remoteUrl)
  const parsedUpdatedRemoteUrl = URL.parse(updatedRemoteUrl)
  const protocolsMatch =
    parsedRemoteUrl.protocol !== null &&
    parsedUpdatedRemoteUrl.protocol !== null &&
    parsedRemoteUrl.protocol === parsedUpdatedRemoteUrl.protocol
```

**File:** app/src/lib/stores/updates/update-remote-url.ts (L42-44)
```typescript
  if (protocolsMatch && remoteUrlUnchanged && !urlsMatch) {
    await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
  }
```

**File:** app/src/lib/git/remote.ts (L56-64)
```typescript
/** Changes the URL for the remote that matches the given name  */
export async function setRemoteURL(
  repository: Repository,
  name: string,
  url: string
): Promise<true> {
  await git(['remote', 'set-url', name, url], repository.path, 'setRemoteURL')
  return true
}
```

**File:** app/src/lib/stores/app-store.ts (L1-1)
```typescript
import * as Path from 'path'
```

**File:** app/src/ui/lib/enterprise-validate-url.ts (L14-45)
```typescript
export function validateURL(address: string): string {
  // ensure user has specified text and not just whitespace
  // we will interact with this server so we can be fairly
  // relaxed here about what we accept for the server name
  const trimmed = address.trim()
  if (trimmed.length === 0) {
    const error = new Error('Unknown address')
    error.name = InvalidURLErrorName
    throw error
  }

  let url = URL.parse(trimmed)
  if (!url.host) {
    // E.g., if they user entered 'ghe.io', let's assume they're using https.
    address = `https://${trimmed}`
    url = URL.parse(address)
  }

  if (!url.protocol) {
    const error = new Error('Invalid URL')
    error.name = InvalidURLErrorName
    throw error
  }

  if (url.protocol !== 'https:') {
    const error = new Error('Invalid protocol')
    error.name = InvalidProtocolErrorName
    throw error
  }

  return address
}
```
