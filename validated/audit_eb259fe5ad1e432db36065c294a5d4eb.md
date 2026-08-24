### Title
Silent, unconfirmed rewrite of a repository's `origin` remote URL based on stale cached `cloneURL` comparison - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl()` is invoked automatically during repository refresh/fetch flows (`repositoryWithRefreshedGitHubRepository` → `withRefreshedGitHubRepository` → `_fetch`/`_fetchRemote`/`_fetchRefspec`) and silently runs `git remote set-url` on the user's `origin` remote whenever a stale, locally-cached `gitHubRepository.cloneURL` "matches" the current remote but a freshly fetched `apiRepo.clone_url` differs. This is the same root pattern as the audited report: a piece of trust-bearing state (the funding price / here, the previously cached `cloneURL`) is used to authorize a state-changing operation without confirming it is still an accurate, trustworthy source of truth, and the result silently corrupts what the user later pushes to.

### Finding Description
`repositoryWithRefreshedGitHubRepository` fetches the tracked repo's current API data by `owner/name` and passes both the stale, DB-cached `gitHubRepository` (containing the old `cloneURL`) and the newly fetched `apiRepo` into `updateRemoteUrl`: [1](#0-0) 

Inside `updateRemoteUrl`, the decision to overwrite the remote is based entirely on comparing the locally cached `gitHubRepository.cloneURL` (last time this was fetched) against the current git remote, and comparing the *new* `apiRepo.clone_url` against the same remote: [2](#0-1) 

If `remoteUrlUnchanged` (cached URL still matches remote) is true and the new API URL doesn't match, the code assumes the *canonical* GitHub-side URL simply changed (e.g., a rename) and calls `gitStore.setRemoteURL`, which runs `git remote set-url` with no user prompt and no diff/confirmation shown: [3](#0-2) 

The invariant being relied on is "the cached `cloneURL` we stored is still authoritative and matches the user's remote, so any change from the API must be a legitimate rename." But that cached value is never re-validated for integrity beyond a raw string equality against the current remote — there's no confirmation that the *identity* (numeric GitHub repo id) of the repository referenced by `owner/name` is the same repository the user originally added. `matchGitHubRepository`/`fetchRepository(owner, name)` resolve purely by endpoint/owner/name path, so if the previously-tracked `owner/name` slug becomes available again (owner deletes/renames their repo, or the repo is transferred and the old slug is squatted by another account), a subsequent background fetch will retrieve an `apiRepo` for a *different* underlying repository but with a `clone_url` differing from the stale cached one, satisfying `remoteUrlUnchanged && !urlsMatch`, and Desktop will silently rewrite `origin` to point at the attacker-controlled clone URL.

This mirrors the audited bug precisely: `funding_tick()` trusted `get_synthetic_price()` without validating it was fresh, letting a stale price gate a state-changing validation (`validate_funding_rate`) that then corrupted collateral balances. Here, `updateRemoteUrl()` trusts a stale cached `cloneURL` string-match without validating repository identity, letting it gate a state-changing git operation (`set-url`) that silently corrupts the user's push/fetch destination.

### Impact Explanation
If the remote is silently repointed to an attacker-controlled repository, all subsequent `git push` operations from the unsuspecting user go to that attacker's repository — this can leak private code/history to an attacker and, combined with any credential helper/token reuse for that host, exposes push credentials to an untrusted destination. It also means subsequent `git fetch`/`pull` merges attacker-supplied history into the user's local branches without any explicit user action or warning, which is a "silent corruption of what the user commits or pushes" and can seed the working tree with attacker-controlled content that later gets committed/pushed back. No dialog, diff, or explicit consent step exists in this path (`setRemoteURL` is a raw `git remote set-url` call), so the user has no visibility into the change.

### Likelihood Explanation
This path runs unconditionally as part of routine background/foreground fetch flows (`_fetch`, `_fetchRemote`, `_fetchRefspec` all call `withRefreshedGitHubRepository`), so it is exercised continuously without explicit user action beyond normal app usage. The precondition — a repo slug becoming re-registrable under a different account/repo after being renamed, transferred, or deleted, while Desktop still has a stale cache entry for the old `cloneURL` — is a scenario within the attacker's control on GitHub.com (create/claim the now-available `owner/name`) and does not require any local/physical access, admin rights, or leaked credentials, matching the requested "unprivileged … attacker controls a … GitHub API object" primitive.

### Recommendation
Do not authorize an automatic `git remote set-url` purely from a string comparison against a previously cached `cloneURL`. Instead:
- Key the "is this the same GitHub repository" check on the immutable GitHub repository `id`, not `owner/name` slug/URL matching, before considering any `clone_url` change legitimate.
- If the immutable id differs from what was last associated with the local `GitHubRepository` record, treat it as a distinct repository (do not silently update remotes) and require explicit user confirmation before repointing `origin`.
- Even when the id matches (a genuine rename), surface a one-time confirmation/notification to the user before mutating `origin`'s URL, since it changes where future pushes go.

### Proof of Concept
1. User clones `https://github.com/victim/project` in Desktop; Desktop stores `gitHubRepository.cloneURL = https://github.com/victim/project` and adds git remote `origin` = same URL.
2. `victim` renames/transfers `project` away, and later the `victim/project` slug becomes available (e.g., repo deleted, or ownership changes such that the name is freed).
3. Attacker creates a new repository at the now-free `owner/name` path (`victim/project`), or otherwise causes `api.fetchRepository(owner, name)` to resolve to a different underlying repo with a different `clone_url` while `owner`/`name` used in matching stay the same enough to be resolved by Desktop's `matchGitHubRepository` (which resolves by owner/name, not GitHub id).
4. On the user's next background/foreground fetch, `repositoryWithRefreshedGitHubRepository` calls `api.fetchRepository(owner, name)` and gets the attacker's `apiRepo` with `clone_url` pointing at attacker's repository content: [4](#0-3)  — this feeds `updateRemoteUrl`, which sees `remoteUrlUnchanged` true (stale cache still matches user's `origin`) and `urlsMatch` false (attacker's clone_url differs), and unconditionally calls `gitStore.setRemoteURL('origin', attackerCloneUrl)`.
5. The existing regression tests only assert the update happens when URLs legitimately change and is skipped when protocols mismatch or the cached URL was already manually changed by the user — none of them validate repository *identity*, confirming the guard gap: [5](#0-4) .
6. From this point forward, the user's subsequent pushes/fetches silently target the attacker's repository with no prompt shown.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4887-4907)
```typescript
    const { account, owner, name } = match
    const { endpoint } = account
    const api = API.fromAccount(account)
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

**File:** app/test/unit/stores/updates/update-remote-url-test.ts (L68-94)
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

  it("doesn't update the repository's remote url when the github url is the same", async t => {
    const { gitHubRepository, gitStore } = await createRepository(
      t,
      apiRepository
    )
    assert(gitStore.currentRemote !== null)
    const originalUrl = gitStore.currentRemote.url
    assert.notEqual(originalUrl.length, 0, 'Expected originalUrl to be empty')
    await updateRemoteUrl(gitStore, gitHubRepository, apiRepository)
    assert(gitStore.currentRemote !== null)
    assert.equal(gitStore.currentRemote.url, originalUrl)
  })
```
