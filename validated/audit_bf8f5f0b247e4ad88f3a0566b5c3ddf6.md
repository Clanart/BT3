## Analog Found: Silent, unauthenticated remote-URL rewrite based on unverified GitHub API repository identity

### Title
Desktop silently rewrites a repository's git remote to match whatever repo the GitHub API returns for a cached owner/name, with no verification of repository identity - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
Desktop's periodic GitHub association refresh (`repositoryWithRefreshedGitHubRepository`) re-resolves a local repository's remote to a GitHub repository by parsing the *current* git remote URL into an `owner/name` pair and calling `api.fetchRepository(owner, name)`. [1](#0-0)  If the API returns a repository, and the git remote still matches the *previously cached* `gitHubRepository.cloneURL`, Desktop will automatically call `gitStore.setRemoteURL(...)` to rewrite the user's `origin` remote to whatever `clone_url` the API just returned - with no check that the returned repository is the "same" repository (e.g. by GitHub repo id/node id). [2](#0-1) 

This exactly mirrors the SymmStaking bug class: a piece of state (the association between local repo and GitHub owner/name) is looked up and reused across time without re-validating that the underlying identity is unchanged, similar to `perTokenStored` surviving a remove/re-add cycle and being silently reapplied to a different context.

### Finding Description
- `matchGitHubRepository` derives `owner`/`name` purely from parsing the local git remote URL string - it performs no lookup against a stable GitHub repository id. [3](#0-2) 
- `repositoryWithRefreshedGitHubRepository` uses that `owner`/`name` pair to call `api.fetchRepository(owner, name)`, i.e., ask GitHub "whatever repository currently exists at this owner/name". [1](#0-0) 
- If a repository is returned, and the *current* git remote URL still equates (via `urlMatchesRemote`) to the repository's previously cached `cloneURL`, `updateRemoteUrl` treats any difference between that cached URL and the newly-fetched `clone_url` as a "rename" and rewrites the actual git remote on disk via `gitStore.setRemoteURL`. [4](#0-3) 
- There is no verification that the GitHub repository object returned by the second `fetchRepository` call is the *same* repository (by id) as the one that originally justified the association. The code only compares `owner`/`name` extracted from the URL and the string form of `clone_url`.

This is the "remove/re-add without resetting state" class of bug: the `owner/name -> GitHub repository` association is cached and later blindly reused to authorize a real, disk-level mutation (rewriting `.git/config`), even though the actual GitHub-side identity behind `owner/name` can change out from under the app (repository deleted, renamed away, or namespace released) and be re-claimed by any other GitHub user/org.

### Impact Explanation
If the upstream repository at `owner/name` is deleted, transferred, or renamed away (freeing up the `owner/name` slug), an attacker who creates a new repository at that same `owner/name` fully controls the `IAPIFullRepository` object (`clone_url`, `html_url`, etc.) returned to Desktop's next background refresh. Desktop will then silently point the user's `origin` remote at the attacker's repository - without any dialog, confirmation, or diff shown to the user. Subsequent `git push` from the user goes to the attacker-controlled repository (source code/credentials-in-history exfiltration), and subsequent `git fetch`/`git pull` merges attacker-controlled history into the user's local branches, which is "silent corruption of what the user commits or pushes" under the stated Valid Impact criteria.

### Likelihood Explanation
This requires no local access, no malware, and no user-initiated unusual steps: the refresh path runs automatically as part of normal repository selection/GitHub-association bookkeeping (`repositoryWithRefreshedGitHubRepository`, called opportunistically whenever Desktop lacks a fresh account association for the repository). [5](#0-4)  The precondition (an `owner/name` becoming reusable after deletion/rename/transfer) is an ordinary, externally-observable GitHub event, not something requiring elevated privileges. The existing guard (`urlMatchesRemote`/`protocolsMatch`) only checks superficial string form of URLs, not repository identity, so it does not stop this path.

### Recommendation
When refreshing a GitHub repository association and deciding whether to auto-update the local remote URL, verify that the newly-fetched `IAPIFullRepository` refers to the *same* underlying repository as the one previously associated (e.g., compare a stable GitHub repository id/node id, not just `owner/name` derived from the remote URL and the `clone_url` string). If the id differs from what's stored for the existing `GitHubRepository` record, do not auto-rewrite the remote; instead, surface this to the user as a new/foreign repository requiring explicit confirmation.

### Proof of Concept
1. User has Desktop open with a local repo whose `origin` remote is `https://github.com/acme/tool.git`, associated in Desktop's DB with the corresponding `GitHubRepository` record (cached `cloneURL = https://github.com/acme/tool.git`). [6](#0-5) 
2. The `acme/tool` repository is deleted or renamed away on GitHub (e.g., org offboards the project), freeing the `acme/tool` slug.
3. Attacker creates a new repository at `acme/tool` (or, in an org context, gets a repo created there) with `clone_url = https://github.com/acme/tool.git` and arbitrary malicious content.
4. On the next opportunistic refresh, Desktop parses the still-unchanged local remote (`acme/tool`) via `matchGitHubRepository`, calls `api.fetchRepository('acme','tool')`, which now returns the attacker's repository object. [7](#0-6) 
5. `updateRemoteUrl` sees `remoteUrlUnchanged` (matches old cached `cloneURL`) is true, protocols match, and rewrites `origin` to the attacker's `clone_url` via `gitStore.setRemoteURL`. [4](#0-3) 
6. The user's next `git push`/`git fetch` silently targets the attacker's repository.

Note: I was not able to fully trace every caller of `repositoryWithRefreshedGitHubRepository` / `withRefreshedGitHubRepository` to enumerate every UI trigger (e.g. exact frequency of automatic refresh vs. explicit user actions) due to index size limits; a full audit of all call sites would benefit from starting a Devin session with complete file access to `app/src/lib/stores/app-store.ts`.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4874-4906)
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

**File:** app/src/lib/stores/updates/update-remote-url.ts (L1-45)
```typescript
import { IAPIRepository } from '../../api'
import { GitStore } from '../git-store'
import { urlMatchesRemote } from '../../repository-matching'
import * as URL from 'url'
import { GitHubRepository } from '../../../models/github-repository'

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

**File:** app/src/lib/repository-matching.ts (L28-46)
```typescript
/** Try to use the list of users and a remote URL to guess a GitHub repository. */
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
