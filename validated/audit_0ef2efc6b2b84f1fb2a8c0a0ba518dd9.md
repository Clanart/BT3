### Title
Silent, incremental hijack of a repository's git remote URL via last-known-value drift in `updateRemoteUrl()` - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl()` decides whether to silently rewrite a repository's local git remote URL by comparing the current remote against the *previously cached* `GitHubRepository.cloneURL`, rather than against a fixed, trusted benchmark (e.g., the URL the user originally added/cloned from). This is structurally the same flaw as the reported `_isDepegged()` bug: the "has this drifted too far" check is anchored to the last observed/cached value instead of the true baseline, so an adversary who can influence successive polled values (here, the GitHub API repository object) can walk the trusted value arbitrarily far over multiple small steps, each of which looks "unchanged" to the guard.

### Finding Description
`updateRemoteUrl()` is invoked whenever Desktop refreshes a `GitHubRepository`'s metadata from the API (e.g. background repository refresh) [1](#0-0) . Its logic:

```
const remoteUrl = gitStore.defaultRemote.url
const updatedRemoteUrl = apiRepo.clone_url
const urlsMatch = urlMatchesRemote(updatedRemoteUrl, gitStore.defaultRemote)
...
const remoteUrlUnchanged =
  gitStore.defaultRemote &&
  urlMatchesRemote(gitHubRepository.cloneURL, gitStore.defaultRemote)

if (protocolsMatch && remoteUrlUnchanged && !urlsMatch) {
  await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
}
``` [2](#0-1) 

The `remoteUrlUnchanged` check exists to detect "has the user manually repointed their remote since we last synced it" — but it compares against `gitHubRepository.cloneURL`, which is itself just the last value cached in the local database from the previous API refresh, not the value that was true when the repository was first added or the last value a human actually approved. `gitHubRepository` (and its `cloneURL`) is persisted/updated via `RepositoriesStore` whenever new API data is upserted [3](#0-2) .

Because the anchor point re-baselines itself every time the check runs (each successful `setRemoteURL` also causes the cached `GitHubRepository.cloneURL` to be refreshed to the newest API value on the next upsert), the guard only ever detects a *single-step* deviation from the *immediately preceding* state. It has no memory of the originally trusted remote. This mirrors the reported bug precisely: `_isDepegged()` only caught deviation from the last `updateWeights()` snapshot, allowing cumulative drift past the true threshold to go undetected as long as each individual step stayed under the per-step limit. Here, each individual remote-URL update looks legitimate ("remote hasn't been manually changed since we last saw it"), so a sequence of small, successive API-reported `clone_url` changes can walk the trusted git remote to a completely different, attacker-controlled destination without ever tripping a "the user changed this on purpose, don't touch it" guard — because there is no comparison against the true origin, only against the last cached checkpoint.

`urlMatchesRemote`/`parseRemote` only validate hostname/owner/name shape [4](#0-3) ; they do not pin to a specific trusted host, so a rogue or compromised API endpoint (most plausibly a malicious/compromised GitHub Enterprise Server the user has added an account for) can return arbitrary `clone_url` values across repeated background refreshes.

### Impact Explanation
If successful, this results in Desktop silently rewriting a user's git remote (`origin`) to an attacker-controlled host without any user-visible confirmation dialog (unlike, for example, the SSH host-key confirmation flow) [5](#0-4) . Subsequent `git push`/`git fetch` operations would silently transmit the user's commits (and any credentials negotiated over that connection via the askpass trampoline) to the attacker's endpoint — matching the "silent corruption of what the user commits or pushes" and "git remote/proxy response" impact categories.

### Likelihood Explanation
This requires the attacker to control or influence the `IAPIRepository`/`IAPIFullRepository` object returned to Desktop across multiple background refresh cycles — realistically only feasible against a malicious or compromised GitHub Enterprise Server endpoint the victim has already added as an account (not against github.com, whose renames/transfers are infrastructure-controlled). This materially limits real-world reach, and I was not able to fully verify from the index alone how frequently `_refreshGitHubRepositoryInfo`/upsert cycles run in production, nor whether any other guard (outside this file) revalidates against the originally-added remote. This should be treated as a plausible-but-unverified analog requiring further live verification in a background Devin session (e.g., tracing all callers of `updateRemoteUrl` and confirming the persisted `cloneURL` is indeed refreshed on every successful step, and confirming no other invariant blocks the walk).

### Recommendation
Anchor the "has the remote been manually changed" check to an immutable, originally-recorded trusted URL (e.g., captured once when the repository/GitHubRepository association was first created) instead of the continuously-updated cached `cloneURL`. Alternatively, require explicit user confirmation (similar to the `AddSSHHost` dialog) before silently rewriting `origin` in response to API-reported changes, and/or bound the automatic rewrite to same-host/verified-owner transfers only.

### Proof of Concept
Conceptual (not fully verified against live infra):
1. Victim adds a GitHub Enterprise Server account under attacker's control (or a legitimately-added GHES instance is later compromised).
2. On refresh cycle 1, the API returns `clone_url = https://ghes.victim-org.com/owner/repo-renamed.git` (a small, plausible-looking change). `updateRemoteUrl()` sees `remoteUrlUnchanged=true` (matches last cached `cloneURL`) and `urlsMatch=false`, so it silently calls `gitStore.setRemoteURL(...)`, and the new value becomes the new cached `cloneURL` baseline.
3. On refresh cycle 2, the API returns `clone_url = https://attacker.evil.com/owner/repo-renamed.git`. Because the check only compares to the just-updated cached baseline (which now matches the current remote), it again reports `remoteUrlUnchanged=true` and silently rewrites `origin` to the attacker's host.
4. The next `git push` from the victim sends their commits (and credential material) to `attacker.evil.com`, with no dialog or warning shown to the user at any step.

### Citations

**File:** app/src/lib/stores/updates/update-remote-url.ts (L7-20)
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
```

**File:** app/src/lib/stores/updates/update-remote-url.ts (L36-44)
```typescript
  // Check if the default remote url has been manually changed from the
  // clone url retrieved from the GitHub API previously
  const remoteUrlUnchanged =
    gitStore.defaultRemote &&
    urlMatchesRemote(gitHubRepository.cloneURL, gitStore.defaultRemote)

  if (protocolsMatch && remoteUrlUnchanged && !urlsMatch) {
    await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
  }
```

**File:** app/src/lib/stores/repositories-store.ts (L1-30)
```typescript
import {
  RepositoriesDatabase,
  IDatabaseGitHubRepository,
  IDatabaseProtectedBranch,
  IDatabaseRepository,
  getOwnerKey,
} from '../databases/repositories-database'
import { Owner } from '../../models/owner'
import {
  GitHubRepository,
  GitHubRepositoryPermission,
} from '../../models/github-repository'
import {
  Repository,
  RepositoryWithGitHubRepository,
  assertIsRepositoryWithGitHubRepository,
  isRepositoryWithGitHubRepository,
} from '../../models/repository'
import { fatalError, assertNonNullable, forceUnwrap } from '../fatal-error'
import {
  IAPIRepository,
  IAPIBranch,
  IAPIFullRepository,
  GitHubAccountType,
} from '../api'
import { TypedBaseStore } from './base-store'
import { WorkflowPreferences } from '../../models/workflow-preferences'
import { clearTagsToPush } from './helpers/tags-to-push-storage'
import { IMatchedGitHubRepository } from '../repository-matching'
import { shallowEquals } from '../equality'
```

**File:** app/src/lib/repository-matching.ts (L90-118)
```typescript
export function urlMatchesRemote(url: string | null, remote: IRemote): boolean {
  if (url == null) {
    return false
  }

  const cloneUrl = parseRemote(url)
  const remoteUrl = parseRemote(remote.url)

  if (remoteUrl == null || cloneUrl == null) {
    return false
  }

  if (!caseInsensitiveEquals(remoteUrl.hostname, cloneUrl.hostname)) {
    return false
  }

  if (remoteUrl.owner == null || cloneUrl.owner == null) {
    return false
  }

  if (remoteUrl.name == null || cloneUrl.name == null) {
    return false
  }

  return (
    caseInsensitiveEquals(remoteUrl.owner, cloneUrl.owner) &&
    caseInsensitiveEquals(remoteUrl.name, cloneUrl.name)
  )
}
```
