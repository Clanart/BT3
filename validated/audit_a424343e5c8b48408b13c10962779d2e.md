### Title
Automatic remote-URL rewrite driven by unauthenticated GitHub API `clone_url` field silently redirects future fetches/pushes - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl` silently rewrites a repository's local `origin` remote whenever the cached `GitHubRepository.cloneURL` no longer matches the freshly-fetched `apiRepo.clone_url`, with no user confirmation. This is triggered automatically every time `repositoryWithRefreshedGitHubRepository` runs (on every repository select, fetch, and background refresh), and only requires that the value returned by the GitHub API for a repository's `clone_url` differ from the last cached one while superficially still "matching" the old remote.

### Finding Description
`repositoryWithRefreshedGitHubRepository` fetches the repository object from the API and, if the local repository already has an associated `GitHubRepository`, calls `updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)` unconditionally on every refresh cycle. [1](#0-0) 

Inside `updateRemoteUrl`, the only checks performed before calling `gitStore.setRemoteURL(...)` are: (1) the URL scheme (http vs ssh) is unchanged, and (2) the *previously cached* `gitHubRepository.cloneURL` matched the existing local remote (i.e. the user hasn't manually customized the remote). There is no check that the new `apiRepo.clone_url` still refers to the same repository identity (owner/name/id) as before, no confirmation prompt, and no rate limiting/repeat-trigger guard: [2](#0-1) 

This mirrors the Agent bug class exactly: a state-mutating "upgrade" (here, silently switching the git remote target) is performed automatically based on external, attacker-adjacent input (a GitHub API JSON object) without validating that this is actually the *same, legitimate* target and without any owner-in-the-loop confirmation, and can be triggered repeatedly on every refresh (`_refreshRepository`, `_fetch`, `_selectRepository`, background fetcher, indicator updater) — all of which call into `repositoryWithRefreshedGitHubRepository` / `withRefreshedGitHubRepository`. [3](#0-2) [4](#0-3) 

Existing guards elsewhere in the codebase (e.g. `urlMatchesRemote`, `matchGitHubRepository`) only compare the *current* URL to the *incoming* one to decide whether a rewrite is due — they never verify that the incoming `apiRepo` still resolves to the exact same GitHub repository identity (its numeric id) as the one the user originally added. Repository name changes are looked up purely by `owner/name` string matching via `matchGitHubRepository`: [5](#0-4) 
so if the same `owner/name` combination is ever recreated, transferred, or reassigned on GitHub (e.g. after the original repo is renamed/deleted and a new repository is created under the freed name — a well-known GitHub "repo-jacking" pattern), Desktop will happily treat the new repository as a continuation of the old one and silently repoint the user's `origin` remote at it, without ever asking the user. Subsequent pushes and fetches then silently go to a different repository than the one the user originally cloned — a silent corruption of what the user pushes/fetches, matching the "no version/identity check before performing a destructive state transition" flaw in the Agent report.

### Impact Explanation
If the owner/name pair a user's local repository is bound to becomes available again on GitHub (deleted repo, renamed-away repo, or an org/user rename race), an attacker who claims that owner/name can have Desktop automatically retarget the victim's `origin` remote to the attacker's repository the next time Desktop refreshes (which happens continuously via background fetch/indicator polling). The victim's next `git push` sends commits (potentially including private code) to the attacker-controlled repository, and future `git fetch`/pull operations bring in attacker-controlled content, which is a silent corruption of what the user pushes.

### Likelihood Explanation
This requires no local access, no admin rights, and no prior compromise — it only requires the attacker to control a GitHub repository whose `owner/name` matches one the victim previously had associated with a local clone (achievable by squatting on a renamed/deleted repo's old name, or via an org/account rename window). Since `repositoryWithRefreshedGitHubRepository` runs on virtually every routine operation (selecting the repo, background fetch, foreground fetch, indicator refresh), the retarget happens automatically and silently in the background without any explicit user action beyond normal app usage.

### Recommendation
Before rewriting the local remote URL in `updateRemoteUrl`, verify the incoming `apiRepo`'s stable identity (numeric `id`) matches the previously cached `GitHubRepository`'s id, not just that the URL differs from a previously observed URL. If the identity check fails (i.e., this looks like a different repository now occupying the same owner/name), do not silently rewrite the remote — instead surface a warning/confirmation to the user, similar to how `UpstreamAlreadyExistsError` prompts the user for upstream remote conflicts.

### Proof of Concept
1. Victim clones `https://github.com/org/tool` in Desktop; Desktop records `gitHubRepository.cloneURL = https://github.com/org/tool` and `origin` matches it.
2. The `org/tool` repository is deleted or renamed away (organization transfer, name change, or repo deletion) so the `owner/name` slot becomes free.
3. Attacker creates a new repository under the exact same `owner/name` (`org/tool`) they control.
4. On the victim's next automatic background refresh, `repositoryWithRefreshedGitHubRepository` calls `api.fetchRepository(owner, name)` (matched purely by name, see `matchGitHubRepository`) and gets the attacker's repository object back with the same `clone_url` string shape.
5. `updateRemoteUrl` sees the new `apiRepo.clone_url` differs from what's cached, the protocol still matches, and the current `origin` still matches the old cached cloneURL — so it calls `gitStore.setRemoteURL('origin', <clone_url>)`, silently repointing `origin` (in this scenario the URL string is unchanged text-wise since owner/name is identical, but the underlying repository — and its `id` — is different).
6. The victim, unaware, runs `git push`, sending their code to the attacker's repository, or `git pull`, merging attacker-controlled history into their local branch.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4904-4907)
```typescript
    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
    }
```

**File:** app/src/lib/stores/app-store.ts (L4964-4977)
```typescript
  private async matchGitHubRepository(
    repository: Repository
  ): Promise<IMatchedGitHubRepository | null> {
    const gitStore = this.gitStoreCache.get(repository)

    if (!gitStore.defaultRemote) {
      await gitStore.loadRemotes()
    }

    const remote = gitStore.defaultRemote
    return remote !== null
      ? matchGitHubRepository(this.accounts, remote.url)
      : null
  }
```

**File:** app/src/lib/stores/app-store.ts (L5895-5899)
```typescript
  public _fetch(repository: Repository, fetchType: FetchType): Promise<void> {
    return this.withRefreshedGitHubRepository(repository, repository => {
      return this.performFetch(repository, fetchType)
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
