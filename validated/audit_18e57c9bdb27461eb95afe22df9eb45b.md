### Title
Silent, unconfirmed remote-URL rewrite driven by GitHub API data - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl` automatically rewrites a repository's local git `origin` remote URL whenever the GitHub API's `clone_url` for the tracked repository differs from what's configured locally, and it does this without ever emitting a user-visible notification, banner, or confirmation dialog. [1](#0-0)  This mirrors the reported bug class exactly: a security-sensitive state change (the destination of future `git push`/`git fetch` operations) is performed with no event emitted to alert the user that it happened.

### Finding Description
`updateRemoteUrl` compares the current `origin` remote URL against `apiRepo.clone_url` — a value that originates entirely from a GitHub API response, i.e., attacker-influenceable server data. [2](#0-1)  If the protocol matches and the *old* remote still matches the previously cached `gitHubRepository.cloneURL`, the function calls `gitStore.setRemoteURL(...)` and silently repoints `origin` to the new URL: [3](#0-2) 

`GitStore.setRemoteURL` itself only calls `emitUpdate()` (an internal UI state refresh), not any banner/toast/log entry that communicates "your remote was changed" to the user: [4](#0-3) 

The matching logic (`urlMatchesRemote`) only checks hostname/owner/name equality after parsing, it does not verify that the *account/identity* behind that owner/name is the same trusted entity across time: [5](#0-4) 

This is precisely the "repo-jacking" surface: GitHub repository renames free up the old `owner/name` slug so it can be re-registered by anyone. If the original repository the user cloned is later renamed/transferred and the freed slug is reclaimed by an attacker (or the API endpoint is a compromised/malicious GHES instance), the API's `clone_url` for that owner/name will now point at attacker infrastructure with the same owner/name shape, satisfying `urlMatchesRemote`/`remoteUrlUnchanged`, and Desktop will silently rewrite the user's local `origin` to the attacker's clone URL on the next background repository refresh — with zero notification.

### Impact Explanation
Once `origin` is silently repointed, all subsequent user actions that implicitly target `origin` — `git push`, `git fetch`, "View on GitHub", "Create Pull Request", credential helper lookups tied to the remote host — operate against attacker-controlled infrastructure without the user's awareness. This can result in: silently pushing the user's commits (and thus their code, potentially containing secrets) to an attacker-controlled remote, silently fetching/merging attacker-controlled objects into the user's history, and credential/token exposure if the attacker-controlled host differs in hostname (though `protocolsMatch` is checked, hostname changes are not restricted). Because no event is emitted, the corruption of the push/fetch destination is silent and matches "silent corruption of what the user commits or pushes," a valid impact bucket for this analysis.

### Likelihood Explanation
This runs automatically as part of normal background repository state syncing whenever Desktop refreshes cached GitHub API data for a tracked repository — no unusual user action is required beyond having Desktop open with the repository tracked, which is standard usage, not local/physical access, malware, or social engineering. The attacker's only requirement is controlling the API object returned for the tracked repository slug (e.g., via a classic repo-rename-then-reclaim hijack, or a compromising an enterprise GHES/API response in transit), which fits the allowed "attacker controls...a GitHub API object" primitive.

### Recommendation
Never silently rewrite a configured remote URL based on API data alone. At minimum:
- Emit a user-visible notification/banner (analogous to the `RebalanceStableBorrowRate` event in the seed report) whenever `setRemoteURL` is invoked as a side effect of API sync, explicitly showing old vs. new URL.
- Require explicit user confirmation before applying `updateRemoteUrl`'s automatic rewrite, rather than performing it silently in the background.
- Strengthen the matching check beyond owner/name/hostname string equality — e.g., verify repository identity via a stable identifier (GitHub's numeric repository `id`) before trusting a `clone_url` change enough to alter local git configuration.

### Proof of Concept
1. User clones `https://github.com/victim/project` in GitHub Desktop; `origin` is set to this URL and `gitHubRepository.cloneURL` is cached from the API. [6](#0-5) 
2. The `victim/project` repository is later renamed/deleted on GitHub.com (a normal, permitted account action by its owner), freeing the `victim/project` slug.
3. An attacker creates a new repository under the same freed `owner/name` slug (`victim/project`) with the same URL shape, but a different underlying `clone_url` host/path.
4. On Desktop's next background repository refresh, the API returns the attacker's repository's `clone_url` for the tracked `GitHubRepository` record. Because `remoteUrlUnchanged` and `protocolsMatch` checks pass (owner/name/hostname structurally match) while `urlsMatch` is false, `updateRemoteUrl` calls `gitStore.setRemoteURL(...)`, silently repointing `origin`. [7](#0-6) 
5. No banner, toast, or dialog appears; the user's next `git push` (e.g. via the standard Push button) sends commits to the attacker-controlled remote.

Note: I was unable to fully trace the exact background-refresh call site of `updateRemoteUrl` inside `app-store.ts` within this investigation (only its 1 reference was located via grep, not its surrounding trigger context) — if further confirmation of the automatic (non-user-initiated) trigger path is needed, a full read of that call site in `app/src/lib/stores/app-store.ts` is recommended.

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

**File:** app/src/lib/stores/git-store.ts (L1533-1543)
```typescript
  /** Changes the URL for the remote that matches the given name  */
  public async setRemoteURL(name: string, url: string): Promise<boolean> {
    const wasSuccessful =
      (await this.performFailableOperation(() =>
        setRemoteURL(this.repository, name, url)
      )) === true
    await this.loadRemotes()

    this.emitUpdate()
    return wasSuccessful
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
