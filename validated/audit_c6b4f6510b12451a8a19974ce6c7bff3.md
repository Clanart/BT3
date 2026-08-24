### Title
Silent remote-URL rewrite from unpinned owner/name GitHub API lookup enables push-hijack via repo-rename/name-squatting - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
Desktop periodically "refreshes" the GitHub repository associated with a local clone by re-parsing the current `origin` remote URL into an `owner`/`name` pair and calling the GitHub API for that pair, then automatically rewriting the local `origin` URL to whatever `clone_url` the API returns — with no verification that the API result actually corresponds to the *same* repository (no stable ID pinning). This mirrors the CurveDAO report's core theme: code that has "been part of the architecture since inception" trusts a downstream data source without validating equivalence between two different views of "the same" object (`balanceOfAt` vs `totalSupplyAt`), letting an attacker who can make that data source return favorable/attacker-controlled values silently benefit at the victim's expense. Here, the "reward" a user unexpectedly gets is having their `git push` silently redirected to a repository the attacker controls.

### Finding Description
`matchGitHubRepository` derives `owner`/`name` purely by regex-parsing the *current* value of the local git remote URL, matched only against the account's hostname: [1](#0-0) 

This owner/name pair (not any stored numeric repository ID) is then used to hit the GitHub API: [2](#0-1) [3](#0-2) 

The API response (`apiRepo.clone_url`) is then compared against the *previously cached* `gitHubRepository.cloneURL`/current remote, and if the protocol matches and the remote hasn't been manually changed, Desktop silently calls `gitStore.setRemoteURL` to point `origin` at the new URL — no user confirmation, no identity check: [4](#0-3) 

Nowhere in this flow is `GitHubRepository.dbID` (or the API's numeric repo `id`) compared to the previously known repository identity before trusting the new `clone_url`. GitHub repositories can be renamed and their old `owner/name` slug becomes available for squatting by any other user (a well-known "repo-jacking" pattern). If a repository a user has cloned is renamed by its owner and the old name is subsequently claimed by an attacker (registering a new, unrelated repository under the vacated `owner/name`), the next time Desktop calls `fetchRepository(owner, name)` using the *stale, locally-derived* owner/name, the GitHub API will correctly and truthfully return the attacker's now-existing repository at that slug. Desktop treats that truthful-but-wrong-object API response as authoritative and rewrites `origin` to the attacker's `clone_url`.

### Impact Explanation
Because the rewrite happens transparently during routine background operations (`_fetch`, `_pull`, checkout, etc., all of which call `withRefreshedGitHubRepository` → `repositoryWithRefreshedGitHubRepository`): [5](#0-4) [6](#0-5) 

a user's subsequent `git push` (via Desktop's UI, which pushes to `origin`) sends their commits — potentially private source code, secrets in history, etc. — to a repository under the attacker's control, with no dialog, warning, or diff shown to the user. This satisfies the "silent corruption of what the user commits or pushes" and "credential/data exfiltration" impact categories: the attacker did not need local access, admin rights, or social engineering — they only needed to squat a vacated repository name on GitHub (a normal, unprivileged GitHub API action) that a victim happens to have as a stale association.

### Likelihood Explanation
The precondition (owner renames repo → old slug becomes squattable → victim's Desktop still has stale owner/name cached) requires no interaction from the victim beyond normal use of Desktop over time, and repo renaming/squatting is common and well documented in the broader "repojacking" literature for CI/CD and package ecosystems. The `updateRemoteUrl` guard conditions (`protocolsMatch`, `remoteUrlUnchanged`) are trivially satisfiable — the attacker's squatted repo can be created with any protocol/clone URL shape they choose, and `remoteUrlUnchanged` merely checks the user hasn't manually edited `origin`, which is the common case. Difficulty is elevated only by needing the rename+squat sequence to occur; existing tests only validate the narrow update logic in isolation and do not test against repo-identity confusion: [7](#0-6) 

### Recommendation
- **Short term:** Before calling `setRemoteURL` in `updateRemoteUrl`, require that the API response's numeric repository `id` matches the previously stored `GitHubRepository.dbID`/API `id` for this local repository, not just a URL string comparison. Add a confirmation step for the user before silently rewriting `origin`.
- **Long term:** Persist and use GitHub's numeric repository ID as the source of truth for "is this API response about the same repository I already know" throughout `repository-matching.ts` and `update-remote-url.ts`, treating owner/name purely as a display/lookup hint, never as an identity guarantee.

### Proof of Concept
1. Attacker sets up `origin` pointing to `github.com/victim-org/legacy-name`, currently owned by the victim organization, which Alice clones with Desktop (associating `gitHubRepository.cloneURL` = `https://github.com/victim-org/legacy-name.git`, `dbID` = X).
2. `victim-org` later renames `legacy-name` to `new-name` (a routine, unprivileged action by the legitimate owner).
3. Attacker (unprivileged) creates a brand-new repository at the now-vacant slug `victim-org/legacy-name` (repo-name squatting), with `clone_url = https://github.com/attacker-controlled-mirror/legacy-name.git` or similar attacker-controlled clone URL.
4. Alice performs a normal `Fetch`/`Pull`/checkout in Desktop. `matchGitHubRepository` re-parses her *unchanged local remote URL* (`.../victim-org/legacy-name.git`) and calls `api.fetchRepository('victim-org', 'legacy-name')`, which now truthfully resolves to the attacker's new repository object [3](#0-2) .
5. `updateRemoteUrl` sees `protocolsMatch = true`, `remoteUrlUnchanged = true` (matches Alice's old cached cloneURL), `urlsMatch = false` (attacker's clone_url differs) — condition `protocolsMatch && remoteUrlUnchanged && !urlsMatch` is satisfied, and Desktop silently rewrites `origin` to the attacker's clone URL [8](#0-7) .
6. Alice's next `git push` in Desktop sends her commits to the attacker's repository without any warning.

### Citations

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

**File:** app/test/unit/stores/updates/update-remote-url-test.ts (L1-129)
```typescript
import { afterEach, describe, it, TestContext } from 'node:test'
import assert from 'node:assert'
import { join } from 'path'
import { GitStore, RepositoriesStore } from '../../../../src/lib/stores'
import { TestRepositoriesDatabase } from '../../../helpers/databases'
import {
  IAPIFullRepository,
  getDotComAPIEndpoint,
} from '../../../../src/lib/api'
import { updateRemoteUrl } from '../../../../src/lib/stores/updates/update-remote-url'
import { shell } from '../../../helpers/test-app-shell'
import { setupFixtureRepository } from '../../../helpers/repositories'
import { addRemote } from '../../../../src/lib/git'
import { TestStatsStore } from '../../../helpers/test-stats-store'

describe('Update remote url', () => {
  const apiRepository: IAPIFullRepository = {
    clone_url: 'https://github.com/my-user/my-repo',
    ssh_url: 'git@github.com:my-user/my-repo.git',
    html_url: 'https://github.com/my-user/my-repo',
    name: 'my-repo',
    owner: {
      id: 42,
      html_url: 'https://github.com/my-user',
      login: 'my-user',
      avatar_url: 'https://github.com/my-user.png',
      type: 'User',
    },
    private: true,
    fork: false,
    default_branch: 'master',
    pushed_at: '1995-12-17T03:24:00',
    has_issues: true,
    archived: false,
    parent: undefined,
  }
  const endpoint = getDotComAPIEndpoint()

  let gitStore: GitStore
  let db: TestRepositoriesDatabase

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

  afterEach(() => {
    db.close()
  })

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

  it("doesn't update repository's remote url if protocols don't match", async t => {
    const originalUrl = 'git@github.com:desktop/desktop.git'
    const sshApiRepository = {
      ...apiRepository,
      clone_url: originalUrl,
    }
    const { gitHubRepository, gitStore } = await createRepository(
      t,
      sshApiRepository
    )
    const updatedUrl = 'https://github.com/my-user/my-updated-repo'
    const updatedApiRepository = { ...apiRepository, clone_url: updatedUrl }

    await updateRemoteUrl(gitStore, gitHubRepository, updatedApiRepository)
    assert(gitStore.currentRemote !== null)
    assert.equal(gitStore.currentRemote.url, originalUrl)
  })

  it("doesn't update the repository's remote url if it differs from the default from the github API", async t => {
    const originalUrl = 'https://github.com/my-user/something-different'
    const { gitHubRepository, gitStore } = await createRepository(
      t,
      apiRepository,
      originalUrl
    )

    const updatedUrl = 'https://github.com/my-user/my-updated-repo'
    const updatedApiRepository = { ...apiRepository, clone_url: updatedUrl }

    await updateRemoteUrl(gitStore, gitHubRepository, updatedApiRepository)
    assert(gitStore.currentRemote !== null)
    assert.equal(gitStore.currentRemote.url, originalUrl)
  })
})
```
