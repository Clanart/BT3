### Title
Silent, unconfirmed re-pointing of a repository's authenticated remote based on GitHub API repo matching by owner/name (no repository-ID pinning) - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
The external report's broken invariant is: a critical trust/authority binding (contract ownership) can be reassigned in a single, unconfirmed step to a party the current authority never explicitly verified, and once done the change cannot be undone. The closest analog in GitHub Desktop is the automatic remote-URL rewrite performed on every push/pull/fetch, which repoints the local `origin` remote — the thing that determines where the user's authenticated git operations (and credentials) go — based on data resolved from the GitHub API by owner/name matching, with no user confirmation step and no verification that the resolved repository is the same underlying entity the user originally trusted.

### Finding Description
`updateRemoteUrl` [1](#0-0)  rewrites the git remote URL to the `clone_url` returned by the GitHub API whenever the protocol matches, the current remote still matches the previously stored `GitHubRepository.cloneURL`, and the new URL differs: [2](#0-1) 

This function is not gated behind any dialog or user acknowledgment. It is invoked from `repositoryWithRefreshedGitHubRepository` [3](#0-2) , which itself runs automatically as part of `withRefreshedGitHubRepository` [4](#0-3)  — a helper called before essentially every normal git network action: `_push` [5](#0-4) , `_pull` [6](#0-5) , `_fetch`/`_fetchRemote`/`_fetchRefspec` [7](#0-6) .

The GitHub repository record used to compute the new clone URL is resolved by `matchGitHubRepository`, which (per its usages in `app-store.ts` and `infer-last-push-for-repository.ts`) derives an `owner`/`name` pair from the *current remote URL string* and then calls `api.fetchRepository(owner, name)` [8](#0-7) . There is no verification against a stable, unique GitHub repository ID recorded at clone time — the match is purely by owner/name text. If the owner/name namespace is ever reclaimed by a different account (e.g., the original repo is renamed/transferred/deleted and the vacated `owner/name` is re-registered by another party — a well-known "repo-jacking" class of risk on GitHub), the API will resolve to a *different* underlying repository, and `updateRemoteUrl`'s guard (`remoteUrlUnchanged` comparing to the previously cached `cloneURL`) will still be satisfied because the cached `GitHubRepository.cloneURL` was set from the same owner/name the last time it was resolved — the code has no independent signal (e.g., GitHub's numeric repository ID) proving repository continuity across the update.

### Impact Explanation
If exploited, the local `origin` remote silently gets rewritten to point at a repository the user never explicitly reviewed or confirmed, and every subsequent authenticated `git push`/`git pull`/`git fetch` (using the user's stored OAuth credentials via the credential helper) targets that new destination. This can result in:
- Silent exfiltration of the user's future commits/pushes to an attacker-controlled repository (loss of control over "what the user pushes", matching the excluded/valid-impact category "silent corruption of what the user commits or pushes").
- Credentials being used against an endpoint the user did not knowingly authorize, since Desktop's git operations route through the (rewritten) remote using the signed-in account's token.

This mirrors the underlying "Ownable" bug class: a security-critical binding (contract owner / here, the authoritative remote target) is changed in one silent step with no independent confirmation from the current authority (the user), and the guard code only checks superficial invariants (protocol match, cached URL match) rather than a strong, non-spoofable identity anchor.

### Likelihood Explanation
This requires a specific real-world precondition — the original `owner/name` on GitHub.com or a GHES host being vacated (renamed/deleted/transferred) and re-claimed by another account — which is a known, previously-reported technique ("repo-jacking") but not something the local attacker can trigger unilaterally against an arbitrary victim repository they don't already influence. It's meaningfully weaker than a fully attacker-triggerable remote path (e.g., a malicious deep link or crafted API object), which is why I flag it as the closest available analog rather than a fully independently confirmed exploit chain. I could not verify the exact implementation of `matchGitHubRepository` (only its call sites were found) within the indexed content, so I cannot confirm with certainty whether it uses only owner/name or also cross-checks a stored repository ID somewhere else in the pipeline.

### Recommendation
Treat the previously-associated `GitHubRepository`'s unique numeric `id` (already stored per `repositories-store.ts`'s `IDatabaseGitHubRepository`) as the authoritative pinning key. Before calling `updateRemoteUrl`, verify that `apiRepo.id` (if available) matches the previously stored `gitHubRepository.dbID`/API id, not just that the owner/name-derived clone URL differs from the cached one. Additionally, surface a one-time confirmation to the user the first time a remote's target repository identity changes (analogous to the two-step transfer-and-accept pattern from the original report), rather than silently rewriting `origin`.

### Proof of Concept
This cannot be fully demonstrated from static code alone because it depends on external GitHub namespace reuse (requires actually renaming/transferring a real GitHub repo and having another account claim the freed `owner/name`), which is outside the scope of local code inspection. The mechanical trigger path, however, is directly reproducible in code:
1. Clone `https://github.com/victim-owner/some-repo` in Desktop; Desktop stores `GitHubRepository{owner: victim-owner, name: some-repo, cloneURL: ...}`.
2. `victim-owner` renames/deletes `some-repo`; `attacker` account creates a new repo at `attacker/some-repo` is not sufficient — the reused-name scenario requires `attacker` to claim the exact vacated `owner-name` slug (e.g., by the org/user name `victim-owner` itself being renamed away and reclaimed, or the repo `some-repo` name being reused after `victim-owner` renames it away).
3. On the user's next `_push`/`_pull`/`_fetch`, `repositoryWithRefreshedGitHubRepository` re-fetches `owner/name` from the API [9](#0-8) , gets the attacker's repo's `clone_url`, and — since `remoteUrlUnchanged` still holds relative to the old cached URL and protocols match — `updateRemoteUrl` silently calls `gitStore.setRemoteURL` [10](#0-9) , repointing `origin` without any dialog.

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

**File:** app/src/lib/stores/app-store.ts (L4886-4907)
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

**File:** app/src/lib/stores/app-store.ts (L5155-5162)
```typescript
  public async _push(
    repository: Repository,
    options?: PushOptions
  ): Promise<void> {
    return this.withRefreshedGitHubRepository(repository, repository => {
      return this.performPush(repository, options)
    })
  }
```

**File:** app/src/lib/stores/app-store.ts (L5452-5456)
```typescript
  public async _pull(repository: Repository): Promise<void> {
    return this.withRefreshedGitHubRepository(repository, repository => {
      return this.performPull(repository)
    })
  }
```

**File:** app/src/lib/stores/app-store.ts (L5895-5915)
```typescript
  public _fetch(repository: Repository, fetchType: FetchType): Promise<void> {
    return this.withRefreshedGitHubRepository(repository, repository => {
      return this.performFetch(repository, fetchType)
    })
  }

  /**
   * Fetch a particular remote in a repository.
   *
   * Note that this method will not perform the fetch of the specified remote
   * if _any_ fetches or pulls are currently in-progress.
   */
  private _fetchRemote(
    repository: Repository,
    remote: IRemote,
    fetchType: FetchType
  ): Promise<void> {
    return this.withRefreshedGitHubRepository(repository, repository => {
      return this.performFetch(repository, fetchType, [remote])
    })
  }
```

**File:** app/src/lib/stores/app-store.ts (L8285-8306)
```typescript
  private async withRefreshedGitHubRepository<T>(
    repository: Repository,
    fn: (repository: Repository) => Promise<T>
  ): Promise<T> {
    let updatedRepository = repository
    const account: Account | null = getAccountForRepository(
      this.accounts,
      updatedRepository
    )

    // If we don't have a user association, it might be because we haven't yet
    // tried to associate the repository with a GitHub repository, or that
    // association is out of date. So try again before we bail on providing an
    // authenticating user.
    if (!account) {
      updatedRepository = await this.repositoryWithRefreshedGitHubRepository(
        repository
      )
    }

    return fn(updatedRepository)
  }
```
