### Title
Auto-adopted remote URL update trusts GitHub API `clone_url` without re-validating repository identity - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl()` silently rewrites a tracked repository's `origin` remote URL whenever the GitHub API reports a different `clone_url` for the same `GitHubRepository` record, gated only by a protocol match and a "did the user manually change it" heuristic — not by re-confirming that the API object still refers to the same underlying repository. This mirrors the SushiMaker bug class: a downstream operation (here, silently repointing the user's push/fetch target) is executed based on an implicit invariant ("the API's `clone_url` for this tracked repo is still the repo the user intended") that is never explicitly re-verified against a strong identifier before being trusted.

### Finding Description
`updateRemoteUrl` compares the current `defaultRemote.url` against `apiRepo.clone_url` and, if the protocol matches and the remote hasn't been manually changed away from the previously-cached `gitHubRepository.cloneURL`, calls `gitStore.setRemoteURL()` to overwrite the local remote with the new URL from the API: [1](#0-0) 

The only checks performed are:
- protocol equality between old and new URL (`URL.parse(...).protocol`)
- that the current remote still matches the *previously cached* `gitHubRepository.cloneURL` (i.e. the user hasn't hand-edited it)

Neither check verifies that the *owner/name pair* embedded in the new `clone_url` is a legitimate rename/transfer of the same underlying repository (e.g. by repository ID) rather than an unrelated repository that now happens to be associated with the same `GitHubRepository` API object the app is tracking. `urlMatchesRemote`, used elsewhere for repo identity checks, only compares hostname/owner/name string components parsed via `parseRemote`: [2](#0-1) 

This is the same broken-invariant pattern as the Sushi/Badger/Digg bug: a piece of state (there, the LP-fee bridge configuration; here, the trusted remote URL) is updated/consumed based on attacker-influenceable input (there, a newly listed pool; here, GitHub API repository metadata such as `clone_url`, which changes on rename/transfer, and which is also reachable through GitHub Enterprise Server endpoints configured by the user) without an explicit check that the *new* value still corresponds to the same trusted entity the user originally set up.

I was not able to fully trace how `apiRepo` is obtained upstream (the `fetchRepository`/`repositoryWithRefreshedGitHubRepository` call sites in `app/src/lib/stores/app-store.ts`) within the available context, so I cannot confirm whether the app queries by immutable repository ID (which would make renames safe to follow) or by owner/name (which is vulnerable to repo-name-squatting after a rename/deletion, a well-known "repojacking" technique). This is a limitation of the indexed context, not a confirmed absence of a guard.

### Impact Explanation
If the API-reported `clone_url` for a tracked repository can be influenced to point at an attacker-controlled repository — e.g. via a renamed/deleted-then-squatted repository on GitHub or GHES, or a compromised/malicious GitHub Enterprise Server the user has authenticated Desktop against — Desktop will automatically rewrite the user's `origin` remote to point at that attacker repository. Subsequent user actions (push, fetch, pull) then silently target the attacker's repository: the user's commits/pushes could be sent to an attacker-controlled destination (credential/commit exfiltration), or the user could unknowingly fetch and merge attacker-controlled history into their local clone. This matches "silent corruption of what the user commits or pushes" in the stated impact criteria.

### Likelihood Explanation
Likelihood is moderate to low without further evidence, because it depends on details not resolved in this pass: whether the API repository lookup is ID-based (safe) or owner/name-based (unsafe), and whether an attacker can realistically get such an API response served to a legitimate Desktop client (via GHES compromise or repo-name squatting on github.com after a rename). No local access, malware, or leaked credentials are required — the trigger is purely a change in the GitHub API's `clone_url` field for a repository the victim already has open in Desktop, which is within the described "attacker controls a GitHub API object" impact class.

### Recommendation
Before calling `setRemoteURL`, re-verify that the new `clone_url` still refers to the same repository by comparing against an immutable identifier (e.g. `gitHubRepository.dbID` / GitHub's numeric repository ID) rather than only string owner/name/hostname matching, and require this to be re-confirmed via an authenticated API call keyed by that ID rather than trusting a cached record. Consider surfacing a confirmation prompt to the user before automatically changing `origin`'s URL, especially when the owner or hostname component changes.

### Proof of Concept
Conceptual PoC (exact triggering path in `app-store.ts` was not confirmed in this session):
1. User adds/clones a GitHub repository `victim/repo` in Desktop; `GitHubRepository.cloneURL` is cached.
2. The repository is renamed or deleted upstream, and an attacker claims the vacated `owner/name` (or the user's GHES endpoint is otherwise made to serve a manipulated `clone_url` for the same tracked API object).
3. On the next repository metadata refresh, `apiRepo.clone_url` returned by the API is now `https://github.com/attacker/repo`.
4. `updateRemoteUrl` sees protocols match and the remote is unchanged from the last known good `cloneURL`, so it calls `gitStore.setRemoteURL('origin', 'https://github.com/attacker/repo')` without any additional identity check: [3](#0-2) 
5. Future `git push`/`git fetch` on `origin` now target the attacker's repository.

### Citations

**File:** app/src/lib/stores/updates/update-remote-url.ts (L7-45)
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

  // Check if the default remote url has been manually changed from the
  // clone url retrieved from the GitHub API previously
  const remoteUrlUnchanged =
    gitStore.defaultRemote &&
    urlMatchesRemote(gitHubRepository.cloneURL, gitStore.defaultRemote)

  if (protocolsMatch && remoteUrlUnchanged && !urlsMatch) {
    await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
  }
}
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
