## Analysis

This maps well onto the Derby report's broken invariant: **a value cached while a subsystem was "trusted"/"active" is silently reused to drive a security-relevant action once the trust boundary is re-crossed, without re-validating it against the current state of the source of truth.** In Derby, `exchangeRate` is not refreshed for an "off" vault; when the vault is turned back "on", the stale rate is blindly used for share math, silently corrupting the amounts users receive. The closest analog with an in-scope Desktop attacker primitive (a controlled GitHub API object / repository rename) is `updateRemoteUrl` in `app/src/lib/stores/updates/update-remote-url.ts`, which will rewrite a user's `git` remote URL based on the GitHub API's `clone_url` for a repository whenever the previously-cached association (`gitHubRepository.cloneURL`) still matches the current remote — silently repointing future pushes/fetches without the user's action. [1](#0-0) 

### Title
Silent remote URL rewrite from stale/attacker-influenced GitHub API repository data corrupts push destination - (File: app/src/lib/stores/updates/update-remote-url.ts)

### Summary
`updateRemoteUrl` compares the locally cached `gitHubRepository.cloneURL` (last known API state) against the *current* API response's `clone_url` and, if they differ but the protocol matches and the current git remote still equals the old cached clone URL, silently calls `gitStore.setRemoteURL` to point the user's `origin` remote at the new URL — with no user confirmation.

### Finding Description
`repositoryWithRefreshedGitHubRepository` in `app-store.ts` refetches the associated GitHub repository from the API and, if a `gitHubRepository` already existed, calls `updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)` before persisting the new association: [2](#0-1) 

`updateRemoteUrl` trusts the API's `clone_url` field as authoritative and mutates the local git remote configuration when:
1. protocols match,
2. the *old cached* `gitHubRepository.cloneURL` still matches the current remote (i.e., nobody has manually repointed the remote since Desktop last saw it), and
3. the *new* API `clone_url` differs from the current remote. [3](#0-2) 

The cached `gitHubRepository` record (owner/name/dbID → API lookup) is the "off/on" analog: Desktop never invalidates or re-derives this association when the underlying GitHub repository is renamed, transferred, or when the numeric repo ID is reused (e.g., a repository is deleted and a new, attacker-controlled repository is created under the same owner/name, or a repository is renamed and the old name is claimed by someone else). Because Desktop keys off the previously cached `dbID`/URL rather than re-validating ownership at the time of the rewrite, an attacker who can get a name/owner slot to resolve differently on GitHub's side can cause Desktop's periodic background refresh (`repositoryWithRefreshedGitHubRepository`, invoked e.g. on `_selectRepository`, `_addRepositories`, `withRefreshedGitHubRepository`) to silently rewrite the local `origin` remote URL to the attacker's repository. [4](#0-3) [5](#0-4) 

No user prompt, diff, or confirmation dialog is shown before the remote is rewritten — the test suite confirms the update happens unconditionally as long as the guard conditions hold: [6](#0-5) 

### Impact Explanation
If the remote is silently repointed to an attacker-controlled repository, all subsequent `git push` operations (and any credential handoff performed by the trampoline/askpass flow for that remote's host) go to the attacker's endpoint instead of the user's real repository. This is silent corruption of what the user pushes (their commits/branches end up hosted by an attacker instead of, or in addition to, their intended destination) and can also leak private code/history to the attacker. This matches the "silent corruption of what the user commits or pushes" and potential credential-exposure categories in scope.

### Likelihood Explanation
The trigger path (`repositoryWithRefreshedGitHubRepository`) runs routinely — on repository selection, on account refresh, after adding repositories, and via `withRefreshedGitHubRepository` before several git operations — so no unusual user action is required beyond normal use of Desktop. The attacker-controlled input is the GitHub API repository object (`clone_url`) returned for a `owner/name` pair Desktop already associated with the local repo; this can shift due to repository deletion+recreation, renames, or ownership transfer on GitHub's side, none of which require local/physical access or leaked credentials. That said, exploitation requires the attacker to successfully claim the specific `owner/name` (or otherwise cause the API to return a different `clone_url` for the same cached `dbID`) before the victim's next refresh — a real but non-trivial precondition, which is why this should be treated as a plausible but not certain path (comparable to the "medium" severity noted in the original Derby report for a similarly infrequent trigger condition).

### Recommendation
Do not silently rewrite the remote URL based solely on matching the previously cached `gitHubRepository.cloneURL`. Before calling `gitStore.setRemoteURL`, re-verify that the new `apiRepo` still corresponds to the same underlying repository identity (e.g., compare stable `id`/`node_id` from the API alongside owner/name, not just URL string matching), and/or prompt the user to confirm remote URL changes when the owner/repo changes materially (e.g., a rename across owners, or a different repository ID under the same owner/name).

### Proof of Concept
Conceptual reproduction based on the existing unit test harness:
1. Desktop has repository R cloned locally with `origin` = `https://github.com/owner/repo` and a cached `GitHubRepository` record matching that URL (`gitHubRepository.cloneURL === origin url`), as set up in `createRepository` in the test file. [7](#0-6) 
2. The GitHub repository `owner/repo` is deleted and recreated by an attacker (or renamed away and reclaimed), so a subsequent `api.fetchRepository(owner, name)` call in `repositoryWithRefreshedGitHubRepository` returns an `apiRepo` with the same `owner/name` but a different underlying repository (different `clone_url` casing/host is not even required — any different `clone_url` under the same protocol qualifies). [8](#0-7) 
3. On the next background refresh (repository selection, account switch, or periodic indicator refresh), `updateRemoteUrl` sees `remoteUrlUnchanged === true` (cached cloneURL still equals current remote) and `urlsMatch === false` (new API clone_url differs), so it calls `gitStore.setRemoteURL(...)` and silently repoints `origin` to the attacker's `clone_url`, exactly as demonstrated by the passing test `"updates the repository's remote url when the github url changes"`. [6](#0-5) 
4. The next `git push` from the user's working copy now targets the attacker's repository without any warning shown in the UI.

Note: I could not find any confirmation dialog, diff view, or user-visible notification tied to `setRemoteURL` calls triggered from `updateRemoteUrl` in the indexed code — if one exists elsewhere in the UI layer, it was not found via the available searches, and a full audit would require a Devin session with complete repository access to be certain no such guard exists.

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

**File:** app/test/unit/stores/updates/update-remote-url-test.ts (L42-62)
```typescript
  const createRepository = async (
    t: TestContext,
    apiRepo: IAPIFullRepository,
    remoteUrl: string | null = null
  ) => {
    db = new TestRepositoriesDatabase()
    await db.reset()
    const repositoriesStore = new RepositoriesStore(db)

    const repoPath = await setupFixtureRepository(t, 'test-repo')
    const repository = await repositoriesStore.setGitHubRepository(
      await repositoriesStore.addRepository(repoPath, join(repoPath, '.git')),
      await repositoriesStore.upsertGitHubRepository(endpoint, apiRepo)
    )
    await addRemote(repository, 'origin', remoteUrl || apiRepo.clone_url)
    gitStore = new GitStore(repository, shell, new TestStatsStore())
    await gitStore.loadRemotes()
    const { gitHubRepository } = repository

    return { gitHubRepository, gitStore }
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
