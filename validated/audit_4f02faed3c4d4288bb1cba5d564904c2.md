### Title
Silent, unprompted rewrite of a repository's `origin` remote URL driven by an untrusted GitHub API field - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
GitHub Desktop automatically rewrites a local repository's tracked remote URL whenever it detects that the "matched" GitHub repository's `clone_url` (a field returned by the GitHub API) differs from what's currently configured in `.git/config`. This write happens without any user prompt via `GitStore.setRemoteURL`, and the decision to trust the new URL is based on comparing it against a locally cached (previously fetched) value rather than continuously validated, authoritative state — the same "cached value diverges from source of truth" pattern as the Volt `vcon`/`CoreRef` report, but here the divergence is exploited in the other direction: a value controlled by the remote GitHub object silently overwrites what the user configured.

### Finding Description
`updateRemoteUrl` in [1](#0-0)  is invoked from `AppStore.repositoryWithRefreshedGitHubRepository` every time the app refreshes a repository's associated GitHub metadata: [2](#0-1) .

The function computes:
- `remoteUrl` = the *currently configured* local remote URL (`gitStore.defaultRemote.url`)
- `updatedRemoteUrl` = `apiRepo.clone_url`, taken directly from the GitHub API response for the matched repository [3](#0-2) 

It then checks that the protocol hasn't changed and that the *previously cached* `gitHubRepository.cloneURL` (stored in the local repositories database from an earlier fetch) still matches the current remote — i.e. `remoteUrlUnchanged` [4](#0-3) . If those two conditions hold and the URLs differ, it silently calls `gitStore.setRemoteURL(...)` to overwrite the remote [5](#0-4) , which shells out to `git remote set-url` [6](#0-5) .

The core issue mirrors the report's bug class: the app makes a trust decision (whether it's "safe" to auto-update the remote) based on a **stale, locally cached** representation of the GitHub-side state (`gitHubRepository.cloneURL`, set at match/fetch time) rather than continuously re-verifying against the live, authoritative value at the moment of the write. Because `clone_url` is attacker-influenced (any account that can rename or transfer a repository the user tracks changes this field), and the update path performs no origin/host allow-listing (a rename could, in theory, land the clone_url on a different host entirely for GHES/proxied setups; the protocol check only verifies http vs https vs ssh, not hostname), the write silently retargets where the user's next `git push`/`git pull` goes, without any dialog, confirmation, or diff shown to the user.

### Impact Explanation
If exploited, `origin` (or another remote) can be silently repointed to a different clone URL as a side effect of routine repository refresh, without the user's explicit action. Because pushes and pulls subsequently use this rewritten remote (`GitStore.currentRemote`/`defaultRemote`, consumed by `performPush` in `app-store.ts` [7](#0-6) ), this can silently corrupt what the user believes they are pushing to/pulling from — falling squarely within the "silent corruption of what the user commits or pushes" impact category. There is no visible indication in the UI that the remote URL has changed (the change happens as a background side effect of `repositoryWithRefreshedGitHubRepository`, not a user-initiated settings change like the one in `repository-settings.tsx`).

### Likelihood Explanation
Likelihood is moderate: the guard conditions (`protocolsMatch` and `remoteUrlUnchanged`) do restrict the update to the "expected" drift scenario (repo renamed/transferred, user hasn't manually customized the remote), which is the intended, benign use case this code was written for (confirmed by the accompanying test suite in `update-remote-url-test.ts`). However, the trigger for `apiRepo.clone_url` changing is entirely controlled by whoever can rename/transfer the tracked GitHub repository, and Desktop performs this update automatically on every periodic/foreground repository refresh with no user confirmation step and no cross-check against the repository's hostname/owner continuity beyond the cached `cloneURL` snapshot. I was not able to fully verify from local code whether the hostname could actually diverge in the GHES/GHE.com endpoint-migration cases (`endpoint-capabilities.ts` migration logic) or whether server-side redirects/`urlMatchesRemote` normalization would prevent a cross-host retarget — this would need dynamic testing to confirm the exact blast radius. Given the code's own comment about the parallel `safeRemote` mismatch handling in `performPush` explicitly acknowledging the risk of remote/branch desync ("theoretical possibility... out of sync... I have no reason to suspect that's the case"), this pattern of un-verified trust in cached vs. live remote state is a recognized architectural risk area in this codebase.

### Recommendation
- Before silently rewriting a remote URL, re-validate the *live* current value of `gitHubRepository.cloneURL` against the database at write time rather than relying on the value captured earlier in the same refresh cycle.
- Require the new clone URL to match the same hostname/origin as the existing remote (not just protocol) before auto-updating, or otherwise refuse to auto-update across hosts.
- Surface a non-blocking notification (not necessarily a blocking prompt) when Desktop auto-updates a remote URL so users have visibility into changes that affect where their future pushes/pulls go.

### Proof of Concept
Local-code-only trace (I could not execute this end-to-end due to tool limitations, but the following is directly supported by the cited source and its existing unit tests):
1. User clones/adds a repository `owner/repo` tracked at `https://github.com/owner/repo`; Desktop stores `gitHubRepository.cloneURL = https://github.com/owner/repo`.
2. The repository owner renames the repo (or transfers it) such that the GitHub API's `clone_url` for that repository becomes `https://github.com/owner/renamed-repo` (an operation entirely under the attacker/owner's control, requiring no interaction from the victim).
3. On the next periodic refresh, `AppStore.repositoryWithRefreshedGitHubRepository` calls `api.fetchRepository(owner, name)`, gets the new `clone_url`, and calls `updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)` [8](#0-7) .
4. `updateRemoteUrl` finds `protocolsMatch === true` and `remoteUrlUnchanged === true` (the user never touched the remote manually) and `urlsMatch === false`, so it calls `gitStore.setRemoteURL(...)`, silently rewriting `origin` — as directly exercised by the existing test "updates the repository's remote url when the github url changes" [9](#0-8) .
5. The user's subsequent push (`_performPush` → `pushRepo` with `safeRemote.url = remote.url`) silently targets the new URL with no confirmation dialog shown. [10](#0-9) [11](#0-10)

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L5275-5291)
```typescript
      const safeRemote: IRemote = { name: remoteName, url: remote.url }

      if (safeRemote.name !== remote.name) {
        sendNonFatalException(
          'remoteNameMismatch',
          new Error('The current remote name differs from the branch remote')
        )
      }

      const gitStore = this.gitStoreCache.get(repository)
      await gitStore.performFailableOperation(
        async () => {
          let aborted = false
          await pushRepo(
            repository,
            safeRemote,
            branch.name,
```

**File:** app/src/lib/stores/git-store.ts (L1533-1543)
```typescript
  /** Changes the URL for the remote that matches the given name  */
  public async setRemoteURL(name: string, url: string): Promise<boolean> {
    const wasSuccessful =
      (await this.performFailableOperation(() =>
        setRemoteURL(this.repository, name, url)
      )) === true
    await this.loadRemotes()

    this.emitUpdate()
    return wasSuccessful
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
