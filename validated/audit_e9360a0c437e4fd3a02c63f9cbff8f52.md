## Analysis

The Superposition bug is a "wrong value silently substituted for a security‑relevant target" class of defect — a mutation function overwrites a sensitive target that the user must trust (an address that controls emergency behavior) with attacker-influenced/incorrect data, with no independent validation catching the mismatch.

In GitHub Desktop, the closest concrete analog is `updateRemoteUrl()`, which silently rewrites the local `origin` remote — the value that determines where every future `push`/`fetch` goes — based on an untrusted field taken straight from a GitHub API response object, with no user confirmation, unlike the equivalent flow Desktop already implements for the `upstream` remote.

### Title
Local `origin` remote is silently retargeted from an unvalidated GitHub API field with no user confirmation - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl()` automatically executes `git remote set-url origin <url>` using `apiRepo.clone_url`, a field taken from a `IAPIRepository`/`IAPIFullRepository` object returned by the GitHub API, whenever a repository is renamed or transferred on GitHub. This happens silently in the background during routine repository refreshes, with no dialog, warning, or user opt-in — in contrast to the analogous `upstream` remote flow, which explicitly prompts the user via the `UpstreamAlreadyExists` dialog before changing anything.

### Finding Description
`repositoryWithRefreshedGitHubRepository()` fetches repository metadata from the GitHub API for the owner/name currently derived from the local remote, and if the associated `gitHubRepository` already exists, unconditionally calls `updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)`: [1](#0-0) 

`updateRemoteUrl()` then compares the current remote URL against `apiRepo.clone_url` and, if the protocol matches and the current remote still matches the previously cached `cloneURL`, calls `gitStore.setRemoteURL()` — with no user-facing confirmation of any kind: [2](#0-1) 

`setRemoteURL()` then directly executes `git remote set-url`: [3](#0-2) 

`urlMatchesRemote()` only validates hostname/owner/name *structural equality* between the two URLs — it does not, and cannot, verify that the "new" location is actually the same trusted repository, only that it differs from what the app previously knew: [4](#0-3) 

This means any repository admin (not a Desktop admin, not local/physical access — just someone with push/admin rights to the repository the victim has cloned, e.g. a co-maintainer of an open-source project) can rename or transfer the repository on GitHub. The next time Desktop performs a background refresh (`repositoryWithRefreshedGitHubRepository`, invoked e.g. on account changes and other routine repository refresh paths), it will silently rewrite the victim's local `origin` remote to point at the new location — without any confirmation — changing where the victim's next `push`/`fetch` goes.

Contrast this with the existing, safer pattern Desktop uses for the `upstream` remote, which explicitly asks the user before mutating anything: [5](#0-4) 

### Impact Explanation
Because the `origin` remote is silently repointed, the invariant "the user knowingly controls where their commits are pushed/fetched from" is broken without any prompt. An attacker with legitimate write/admin access to a repository the victim has cloned (a routine, unprivileged position for open-source contributors, not requiring local/physical access or leaked credentials) can rename/transfer that repository to redirect victims' local git operations. Concretely this enables:
- Silent corruption of what the user pushes: a proprietary/sensitive push the user believes is going to the known repository is instead redirected to a repository the attacker set up (e.g., after a transfer to an account/org the attacker controls), disclosing code/history to unintended parties.
- No user-visible signal exists (no dialog, no toast) — the only trace is the changed remote URL in Preferences → Repository, which most users never inspect.

This matches the Valid Impact criteria: attacker controls a "GitHub API object" (the `clone_url` field of the fetched repository), and the result is "silent corruption of what the user commits or pushes."

### Likelihood Explanation
The refresh path (`repositoryWithRefreshedGitHubRepository`) runs automatically during normal Desktop usage (e.g. account/selection changes), so no unusual user action is required beyond the attacker renaming/transferring a repository they already have rights to — an ordinary, low-friction action on GitHub. The guard conditions (`protocolsMatch`, `remoteUrlUnchanged`, `!urlsMatch`) only prevent the update from firing on hand-edited remotes; they do nothing to validate that the *new* target is trustworthy, so they do not stop this path.

### Recommendation
Require explicit user confirmation before rewriting the `origin` remote URL in response to GitHub API changes, mirroring the pattern already used for `upstream` (`UpstreamAlreadyExists` dialog) — e.g. surface a dialog showing the old vs. new URL and let the user accept/ignore, rather than calling `gitStore.setRemoteURL()` unconditionally from `updateRemoteUrl()`.

### Proof of Concept
1. Victim clones `https://github.com/alice/project.git` in Desktop and it becomes the tracked `origin` remote, associated with a `GitHubRepository` whose `cloneURL` is `https://github.com/alice/project.git`.
2. `alice` (a legitimate repository admin from Desktop's perspective, no special privileges over the victim) renames/transfers the repository on GitHub so that its canonical location becomes `https://github.com/alice/project-relocated.git` (or transfers ownership to another account she controls).
3. On the victim's next background repository refresh, `repositoryWithRefreshedGitHubRepository()` calls `api.fetchRepository('alice', 'project')`, which — following GitHub's rename/transfer redirect — returns an `IAPIFullRepository` with `clone_url: 'https://github.com/alice/project-relocated.git'`. [6](#0-5) 
4. `updateRemoteUrl()` sees `protocolsMatch = true`, `remoteUrlUnchanged = true` (current remote still equals the cached `cloneURL`), `urlsMatch = false`, and calls `gitStore.setRemoteURL('origin', 'https://github.com/alice/project-relocated.git')` with zero user interaction. [7](#0-6) 
5. The victim's next `git push`/`git fetch` from Desktop silently targets `alice/project-relocated` instead of the repository they originally cloned and expect, with no dialog ever having informed them of the change.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4890-4907)
```typescript
    const apiRepo = await api.fetchRepository(owner, name)

    if (apiRepo === null) {
      // If the request fails, we want to preserve the existing GitHub
      // repository info. But if we didn't have a GitHub repository already or
      // the endpoint changed, the skeleton repository is better than nothing.
      if (endpoint !== repository.gitHubRepository?.endpoint) {
        const ghRepo = await repoStore.upsertGitHubRepositoryFromMatch(match)
        return repoStore.setGitHubRepository(repository, ghRepo)
      }

      return repository
    }

    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
    }
```

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

**File:** app/src/ui/upstream-already-exists/upstream-already-exists.tsx (L14-21)
```typescript
  readonly onDismissed: () => void

  /** Called when the user chooses to update the existing remote. */
  readonly onUpdate: (repository: Repository) => void

  /** Called when the user chooses to ignore the warning. */
  readonly onIgnore: (repository: Repository) => void
}
```
