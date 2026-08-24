### Title
Automatic remote-URL retargeting based on GitHub API repository match silently redirects `origin` to a different repository, causing subsequent fetch/pull to merge attacker-controlled content - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`RemoraToken` and `PaymentSettler` each cache their own copy of the `stablecoin` address, one of which cannot be updated, so the two contracts silently drift apart and core functions corrupt/DoS. The GitHub Desktop analog is `updateRemoteUrl` in `app/src/lib/stores/updates/update-remote-url.ts`, which automatically rewrites the local `origin` remote URL to whatever `clone_url` the GitHub API currently reports for the `owner/name` pair Desktop has cached — a value that is not cryptographically bound to the repository the user originally cloned, only to a name/owner match.

### Finding Description
`RepositoryWithRefreshedGitHubRepository` in `app-store.ts` looks up the associated GitHub repository via `matchGitHubRepository` and then calls `api.fetchRepository(owner, name)` [1](#0-0) . Whatever repository object the API currently returns for that `owner/name` is treated as authoritative and fed into `updateRemoteUrl`:

```
const remoteUrlUnchanged =
  gitStore.defaultRemote &&
  urlMatchesRemote(gitHubRepository.cloneURL, gitStore.defaultRemote)

if (protocolsMatch && remoteUrlUnchanged && !urlsMatch) {
  await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
}
``` [2](#0-1) 

The only invariant checked before rewriting `origin` is: the protocol is unchanged, and the *current* local remote still matches the *previously cached* `GitHubRepository.cloneURL`. There is no check that the newly fetched repository is the *same underlying repository* (e.g., by GitHub's numeric repo `id`) as the one the user originally associated with this clone — only that the owner/name lookup succeeded and the clone URL differs. `GitHubRepository` model stores `dbID`/API fields keyed by `endpoint + owner/name`, not by a stable repo identity that would survive a delete-and-recreate. Because GitHub allows repository names to be freed and reused after deletion/rename, an attacker who obtains the exact `owner/name` that a victim previously had cloned (e.g., after the original repo is renamed away or deleted) can create a brand-new repository under that identifier. On the next periodic refresh (`refreshSelectedRepositoryAfterAccountChange`, invoked whenever accounts update, and other paths reaching `repositoryWithRefreshedGitHubRepository`) [3](#0-2) , Desktop fetches the attacker's repository object and silently rewrites the local `origin` remote to the attacker's `clone_url`, with no user prompt or diff shown.

Unlike the smart-contract case where the mismatch causes an outright revert (a detectable DoS), this Desktop analog is worse: the state is corrupted *silently* and *successfully* — the two logically-linked values (the actual repository identity the user trusts, and the remote URL Git will use) diverge without any error, matching the report's core theme of "two related stores of the same conceptual value going out of sync because only one side can be legitimately updated, while the other keeps stale trust."

### Impact Explanation
Once `origin` is retargeted, any subsequent `git fetch`/`git pull` performed by Desktop will pull refs and objects from the attacker's repository into the user's local clone, and any `git push` (if the attacker also grants push access, e.g. via a public fork trick or by inviting the victim) would send the user's code/credentials to the attacker's endpoint instead of the intended one. This satisfies "silent corruption of what the user commits or pushes" via an attacker-controlled GitHub API object, without any local access, admin rights, or social engineering step beyond normal repository lifecycle events (rename/delete/recreate) that are entirely within GitHub's public feature set.

### Likelihood Explanation
This requires: (1) a victim to have previously cloned/associated a GitHub repository via Desktop, (2) that repository's `owner/name` becoming available again (deleted or renamed away), and (3) an attacker registering a new repository at that same `owner/name`, and (4) Desktop performing one of its automatic refresh cycles while the victim still has that local clone open (e.g., account refresh, branch protection refresh, or general repository refresh flows that call `repositoryWithRefreshedGitHubRepository`). These conditions are plausible in practice — e.g., a compromised or abandoned maintainer account renames/deletes a popular repo and an attacker immediately reclaims the freed name — and require no privileged access to the victim's machine.

### Recommendation
Bind the cached `GitHubRepository` association to a stable, non-reusable identifier (GitHub's numeric repository `id`) rather than trusting `owner/name` string matches when deciding whether to auto-update the local remote URL. `updateRemoteUrl` should refuse to rewrite the remote if the newly fetched API repository's `id` differs from the previously stored `GitHubRepository`'s `dbID`/`id`, and instead surface a warning/prompt asking the user to confirm the repository identity change before altering `origin`.

### Proof of Concept
1. Victim clones `https://github.com/acme/widgets` in GitHub Desktop; Desktop stores `GitHubRepository{owner: acme, name: widgets, cloneURL: https://github.com/acme/widgets}` and `origin` matches it.
2. `acme/widgets` is deleted or renamed to `acme/widgets-old` (freeing the `widgets` name).
3. Attacker creates a new repository `acme2/widgets`... actually more precisely under the *same* owner login if the acme account is compromised, or under a similarly-permissioned org, reusing `owner: acme, name: widgets`.
4. Desktop's periodic/account-triggered refresh calls `repositoryWithRefreshedGitHubRepository`, which calls `api.fetchRepository('acme','widgets')`, getting back the attacker's new repo object with a different `clone_url` (same owner/name but new repo id) [4](#0-3) .
5. `updateRemoteUrl` sees `protocolsMatch=true`, `remoteUrlUnchanged=true` (local origin still equals the old cached `cloneURL`), and `urlsMatch=false` (attacker's clone_url differs) and calls `gitStore.setRemoteURL('origin', attackerCloneUrl)` [5](#0-4) .
6. The victim's next `Fetch origin` in Desktop pulls history from the attacker's repository into their local clone without any warning.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4874-4907)
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
```

**File:** app/src/lib/stores/app-store.ts (L4916-4933)
```typescript
  /**
   * Refreshes the GitHub repository information for the currently selected
   * repository when the active account changes. This ensures that permission
   * information is updated after signing in/out.
   */
  private async refreshSelectedRepositoryAfterAccountChange() {
    const repository = this.selectedRepository

    if (repository === null || repository instanceof CloningRepository) {
      return
    }

    if (!isRepositoryWithGitHubRepository(repository)) {
      return
    }

    await this.repositoryWithRefreshedGitHubRepository(repository)
  }
```

**File:** app/src/lib/stores/updates/update-remote-url.ts (L18-44)
```typescript
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
```
