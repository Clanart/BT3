## Title
Automatic remote URL rewrite from unvalidated GitHub API `clone_url` silently redirects future pushes - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl` in `app/src/lib/stores/updates/update-remote-url.ts` automatically rewrites a repository's git remote URL to whatever value is present in `apiRepo.clone_url` — a field that originates from the GitHub API response for the associated `GitHubRepository` — whenever a small set of heuristic conditions are met, with no user confirmation. [1](#0-0)  The only checks performed are that the URL scheme (`https:`/`ssh:`-ish) is unchanged and that the *previous* cached `cloneURL` matched the current remote — there is no check that the *new* `clone_url` still refers to the same owner/repository identity. [2](#0-1)  This is the same bug class as the audit finding: a narrow, index/heuristic-based guard condition that is supposed to gate a sensitive state-changing action, but that guard doesn't actually validate the *content* being trusted, only whether the action's preconditions look superficially "safe."

### Finding Description
The comparison helper `urlMatchesRemote` (used to compute `urlsMatch` and `remoteUrlUnchanged`) only determines whether two URLs currently match; it is never used to check whether the *new* `updatedRemoteUrl` is consistent with the *old* one (e.g., same owner/repo, legitimate rename). [3](#0-2)  As a result, once the three permissive conditions are true — protocols match, the remote URL hadn't been manually changed from what Desktop last cached as the "official" clone URL, and the new URL differs from the current remote — Desktop will unconditionally call `gitStore.setRemoteURL(...)` with the attacker-supplied `clone_url`, with no restriction on the new URL's owner/repository/host. [4](#0-3)  `setRemoteURL` simply executes `git remote set-url` with the given string. [5](#0-4) 

The existing unit tests only validate benign rename scenarios (owner/repo renamed under the same GitHub host) and never assert that the destination must remain within the same repository network; the test suite's "updates the repository's remote url when the github url changes" case demonstrates the code will happily switch the remote to `https://github.com/my-user/my-updated-repo` with no ownership continuity check. [6](#0-5)  Nothing in the code stops `clone_url` from pointing to a completely different, attacker-controlled repository/host as long as protocol matches and the cached `cloneURL` still matches the current local remote (the common, default state for most users who haven't hand-edited their remote).

This mirrors the audited Solidity bug precisely: a guard intended to make one specific case (a legitimate rename) safe is instead reused as the sole gate for a much broader, unsafe action — the guard checks "did the situation look like the expected case," not "is the target of the action actually trustworthy."

### Impact Explanation
If the field backing `apiRepo.clone_url` can be influenced by a repository owner/maintainer/attacker (e.g., by renaming or transferring a public/shared repo the victim has cloned as a GitHub-linked repository), Desktop will silently repoint the victim's `origin` remote to any attacker-chosen URL the next time the app refreshes GitHub repository metadata — without a prompt, diff, or confirmation dialog. Subsequent `git push` operations by the victim would then be silently redirected to the attacker's endpoint, potentially exfiltrating private commits/branches, or (in credential-helper configurations) leaking push credentials to the attacker's git server. This matches the "silent corruption of what the user commits or pushes" category directly.

### Likelihood Explanation
This code path only runs automatically as part of normal GitHub Desktop repository-refresh flows (i.e., it does not require the user to click anything unusual), and the attacker-influenced input (`clone_url` from the GitHub API object for a tracked repository) is realistically reachable by anyone who controls or can rename/transfer a repository the victim has added to Desktop. The main mitigating factor is the `remoteUrlUnchanged` precondition, which requires the victim's current remote to still match the previously cached `cloneURL` — true for the large majority of users who never hand-edit `origin`. I was not able to fully trace every call site of `updateRemoteUrl` from `app-store.ts` (the outer context of when/how often this refresh triggers was not retrievable within the exploration budget), so the exact refresh cadence/trigger (e.g., on app focus, periodic background refresh, or explicit user action) is uncertain and should be confirmed by a maintainer or via a live Desktop session.

### Recommendation
Do not trust `clone_url` unconditionally. Before rewriting the remote:
1. Re-resolve the *new* `clone_url` via the GitHub API to confirm it is genuinely a rename/transfer of the *same* repository (e.g., by dbID) rather than an arbitrary independent repository.
2. Compare the previous and new repository identity more strictly (e.g., persisted GitHub repository database ID vs. owner/name), not just protocol-level URL shape matching.
3. Surface a confirmation to the user before rewriting `origin`, at least the first time, since this silently changes where future work is pushed.

### Proof of Concept
1. Victim adds/clones a GitHub-hosted repository `R` in GitHub Desktop; Desktop caches `gitHubRepository.cloneURL` and the local `origin` remote both point to `https://github.com/attacker/R`.
2. Attacker (owner of `R`) renames/transfers `R` such that the GitHub API now returns `clone_url: "https://github.com/attacker-controlled-org/malicious-target"` for the same tracked `dbID`.
3. On the next background GitHub repository refresh, `updateRemoteUrl` computes: `protocolsMatch = true` (both https), `remoteUrlUnchanged = true` (local remote still matches the previously cached clone URL), `urlsMatch = false` (URL differs). [7](#0-6) 
4. Desktop calls `gitStore.setRemoteURL('origin', 'https://github.com/attacker-controlled-org/malicious-target')` without any user prompt. [8](#0-7) 
5. The victim's next `git push` silently goes to the attacker-controlled destination.

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

**File:** app/src/lib/stores/updates/update-remote-url.ts (L22-44)
```typescript
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

**File:** app/test/unit/stores/updates/update-remote-url-test.ts (L68-81)
```typescript
  it("updates the repository's remote url when the github url changes", async t => {
    const { gitHubRepository, gitStore } = await createRepository(
      t,
      apiRepository
    )
    assert(gitStore.currentRemote !== null)

    const originalUrl = gitStore.currentRemote.url
    const updatedUrl = 'https://github.com/my-user/my-updated-repo'
    const updatedApiRepository = { ...apiRepository, clone_url: updatedUrl }
    await updateRemoteUrl(gitStore, gitHubRepository, updatedApiRepository)
    assert.notEqual(originalUrl, updatedUrl)
    assert.equal(gitStore.currentRemote.url, updatedUrl)
  })
```
