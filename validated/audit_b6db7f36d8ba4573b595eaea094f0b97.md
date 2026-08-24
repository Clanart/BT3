## Title
Silent, unconfirmed rewrite of a repository's git remote based on unauthenticated ownership (owner/name) matching instead of a stable repository identity - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
GitHub Desktop automatically calls `git remote set-url` on a user's local repository whenever it refreshes the `GitHubRepository` association for a selected repo, using data returned by `GET /repos/{owner}/{name}` for whatever `owner`/`name` the *current* remote URL happens to parse to. This mirrors the "stale trust flag" bug class from the report: a privileged action (rewriting the push/fetch destination) is gated on a piece of state (`repository.gitHubRepository` / `defaultRemote`) that is derived once from a mutable, attacker-influenceable value (owner/name string) rather than from a stable identifier, and the rewrite happens with no user confirmation.

### Finding Description
`matchGitHubRepository` derives the `owner`/`name` used for the API lookup purely by regex-parsing whatever URL is currently set on the repository's default remote: [1](#0-0) 

This match is used in `repositoryWithRefreshedGitHubRepository`, which runs automatically (not on explicit user request) every time a repository is selected or refreshed: [2](#0-1) [3](#0-2) 

It fetches whatever repository currently lives at that `owner/name` from the API (`api.fetchRepository(owner, name)`), and if it differs from the locally cached association, it (a) silently rewrites the local git remote via `updateRemoteUrl`, and (b) re-associates the local `GitHubRepository` (including its `dbID`) with the response, keyed only by `endpoint`/`owner`/`name` — not by any GitHub-assigned stable repository ID: [4](#0-3) [5](#0-4) 

The developers themselves acknowledge the design flaw directly above this code: Desktop "currently never clear[s] GitHub repository associations" (desktop/desktop#1144), so once an association is established it is only ever *refreshed forward* toward whatever the API currently reports for that name — never invalidated: [6](#0-5) 

Because a repository owner (the attacker, if they administer a repo a victim has cloned/added — which requires no privileged access to the victim's machine or account) can rename or transfer *their own* repository at will, and Desktop trusts `apiRepo.clone_url` from that repository object to auto-update the victim's `origin` remote as long as `protocolsMatch && remoteUrlUnchanged && !urlsMatch`, the destination of the victim's future `git push`/`git fetch` operations can be changed by the attacker without any confirmation dialog ever being shown to the user: [7](#0-6) 

Existing guards do not stop this: `remoteUrlUnchanged`/`urlsMatch`/`protocolsMatch` only prevent Desktop from clobbering a remote the user has *manually* customized — they do nothing to verify that the "new" repository behind the same owner/name is the *same underlying GitHub repository* (no dbID/permanent-ID comparison is performed anywhere in this path).

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes": a victim who has cloned an attacker-owned (or attacker later-controlled/renamed/transferred) repository can have their local `origin` remote silently repointed by Desktop, with zero prompt, the next time the repository is selected or auto-refreshed (which happens routinely via `_selectRepositoryRefreshTasks` and the periodic `RepositoryIndicatorUpdater`/background fetcher paths). A subsequent push from the unsuspecting user delivers their commits/work to a destination they never consented to, and the locally stored `GitHubRepository` metadata (permissions, fork/parent info, PR/branch-protection data) is silently swapped to match the attacker's repository object as well, since re-association is keyed on owner/name rather than a stable ID.

### Likelihood Explanation
Likelihood is limited by the fact that the attacker must administer the source repository the victim interacts with (their own repo, which is an explicitly allowed "attacker controls a ... GitHub API object" precondition) and must wait for Desktop's automatic refresh cycle (selection or the 15-minute `RepositoryIndicatorUpdater`/background fetch) to fire. No local access, no leaked credentials, and no social-engineering step beyond the repository being cloned/added (already covered) is required, but the attack is opportunistic rather than instantly reliable, and its full "malicious redirect" strength depends on scenarios (renames/transfers, GitHub repo-name reuse after rename) that are partly outside Desktop's own code and rooted in GitHub's naming semantics — I could not fully verify from local code alone how frequently/reliably a divergent-but-still-resolvable `owner/name` mapping would arise in practice, since that also depends on GitHub server-side behavior not present in this repository.

### Recommendation
Tie the cached `GitHubRepository` association and the automatic `updateRemoteUrl` rewrite to GitHub's stable numeric repository ID rather than to the mutable `owner`/`name` pair. Before silently rewriting a remote or re-associating a repository, verify that the newly fetched `apiRepo.id` (or the repository's `node_id`) matches the previously stored `gitHubRepositoryID`; if it doesn't match, treat this as a strong signal the repository at that URL is not the same one the user originally added, and require explicit user confirmation before proceeding.

### Proof of Concept
1. Attacker creates a public GitHub repository `attacker/lib` and gets a victim to clone it in GitHub Desktop (a normal, unprivileged interaction).
2. Attacker (owning the repo) renames/transfers it so that the GitHub API's `clone_url` for the repository at that same `owner`/`name` combination now differs from what the victim's local remote points to (e.g., via a rename sequence or repository transfer under attacker's control).
3. On the victim's next repository selection, or on the next periodic `RepositoryIndicatorUpdater`/background-fetch cycle, `_selectRepositoryRefreshTasks` → `repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl` fires automatically: [8](#0-7) 
4. Because `protocolsMatch` and `remoteUrlUnchanged` hold (the victim never manually edited the remote) and `urlsMatch` is false (the API's clone_url now differs), Desktop calls `gitStore.setRemoteURL` with the attacker-supplied destination with no prompt: [9](#0-8) 
5. The victim's next `git push` from Desktop is delivered to the attacker-controlled destination without any warning.

### Citations

**File:** app/src/lib/repository-matching.ts (L29-46)
```typescript
export function matchGitHubRepository(
  accounts: ReadonlyArray<Account>,
  remote: string
): IMatchedGitHubRepository | null {
  for (const account of accounts) {
    const htmlURL = getHTMLURL(account.endpoint)
    const { hostname } = URL.parse(htmlURL)
    const parsedRemote = parseRemote(remote)

    if (parsedRemote !== null && hostname !== null) {
      if (parsedRemote.hostname.toLowerCase() === hostname.toLowerCase()) {
        return { name: parsedRemote.name, owner: parsedRemote.owner, account }
      }
    }
  }

  return null
}
```

**File:** app/src/lib/stores/app-store.ts (L2218-2257)
```typescript
  // finish `_selectRepository`s refresh tasks
  private async _selectRepositoryRefreshTasks(
    repository: Repository,
    previouslySelectedRepository: Repository | CloningRepository | null
  ): Promise<Repository | null> {
    this._refreshRepository(repository)

    if (isRepositoryWithGitHubRepository(repository)) {
      // Load issues from the upstream or fork depending
      // on workflow preferences.
      const ghRepo = getNonForkGitHubRepository(repository)

      this._refreshIssues(ghRepo)
      this.refreshMentionables(ghRepo)

      this.pullRequestCoordinator.getAllPullRequests(repository).then(prs => {
        this.onPullRequestChanged(repository, prs)
      })
    }

    // The selected repository could have changed while we were refreshing.
    if (this.selectedRepository !== repository) {
      return null
    }

    // "Clone in Desktop" from a cold start can trigger this twice, and
    // for edge cases where _selectRepository is re-entract, calling this here
    // ensures we clean up the existing background fetcher correctly (if set)
    this.stopBackgroundFetching()
    this.stopPullRequestUpdater()
    this.stopBackgroundPruner()

    this.startBackgroundFetching(repository, !previouslySelectedRepository)
    this.startPullRequestUpdater(repository)

    this.startBackgroundPruner(repository)

    this.addUpstreamRemoteIfNeeded(repository)

    return this.repositoryWithRefreshedGitHubRepository(repository)
```

**File:** app/src/lib/stores/app-store.ts (L4874-4914)
```typescript
  private async repositoryWithRefreshedGitHubRepository(
    repository: Repository
  ): Promise<Repository> {
    const repoStore = this.repositoriesStore
    const match = await this.matchGitHubRepository(repository)

    // TODO: We currently never clear GitHub repository associations (see
    // https://github.com/desktop/desktop/issues/1144). So we can bail early at
    // this point.
    if (!match) {
      return repository
    }

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

    const ghRepo = await repoStore.upsertGitHubRepository(endpoint, apiRepo)
    const freshRepo = await repoStore.setGitHubRepository(repository, ghRepo)

    await this.refreshBranchProtectionState(freshRepo)
    return freshRepo
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

**File:** app/src/lib/stores/repositories-store.ts (L565-594)
```typescript
  public async setGitHubRepository(repo: Repository, ghRepo: GitHubRepository) {
    // If nothing has changed we can skip writing to the database and (more
    // importantly) avoid telling store consumers that the repo store has
    // changed and just return the repo that was given to us.
    if (isRepositoryWithGitHubRepository(repo)) {
      if (repo.gitHubRepository.hash === ghRepo.hash) {
        return repo
      }
    }

    await this.db.transaction('rw', this.db.repositories, () =>
      this.db.repositories.update(repo.id, { gitHubRepositoryID: ghRepo.dbID })
    )
    this.emitUpdatedRepositories()

    const updatedRepo = new Repository(
      repo.path,
      repo.id,
      ghRepo,
      repo.missing,
      repo.alias,
      repo.workflowPreferences,
      repo.isTutorialRepository,
      repo.gitDir,
      repo.mainWorktreePath
    )

    assertIsRepositoryWithGitHubRepository(updatedRepo)
    return updatedRepo
  }
```
