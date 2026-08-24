### Title
Repository re-matching by `owner/name` lets a renamed/deleted repo be silently replaced, causing Desktop to auto-rewrite the local git remote to an attacker's repository - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
This is a structural analog of the reported bug: a piece of trust state (`upliftFeeBps` in the contract; here, the git remote URL bound to a `GitHubRepository`) is silently overwritten based on data supplied by a party the user does not fully control, without re-confirmation, breaking the invariant "the user's tracked remote should not change without the user's consent."

### Finding Description
`matchGitHubRepository` derives the `owner`/`name` pair used to look up a tracked repository purely by parsing the current git remote URL — it never pins to a stable GitHub repository ID: [1](#0-0) 

`app-store.ts`'s `repositoryWithRefreshedGitHubRepository` uses this `owner`/`name` match to call `api.fetchRepository(owner, name)` against the GitHub API, and if it returns a repository, feeds the result into `updateRemoteUrl`: [2](#0-1) 

`updateRemoteUrl` will then silently call `gitStore.setRemoteURL(...)` to rewrite the user's local remote to the API's `clone_url`, gated only by: protocol match, the current remote still matching the *previously cached* `gitHubRepository.cloneURL` ("unchanged"), and the URLs differing: [3](#0-2) 

Because the lookup key is `owner/name` (a mutable, reusable string) rather than the GitHub repository's immutable numeric ID, if the original repository is renamed or deleted and a different party (attacker) subsequently creates/claims a repository at that same `owner/name` slot on the same host, the next background refresh will resolve `apiRepo` to the attacker's repository. All three gating conditions in `updateRemoteUrl` are satisfied in the common case (user never manually edited `origin`, protocol unchanged), so Desktop silently repoints `origin` to the attacker-controlled `clone_url` with no prompt, confirmation, or warning to the user — exactly analogous to `poolsFeeData[pool][_to].push(feeDataArray[tokenIdIndex])` silently overwriting `upliftFeeBps` in the reported contract bug: an implicit "retroactive" state update performed by an internal hook rather than an explicit user action.

### Impact Explanation
Once `origin` is silently repointed, subsequent `git fetch`/`pull` operations will pull the attacker's history into the user's working repository (supply-chain risk: malicious commits merged into the user's tree), and subsequent `git push` operations will push the user's code/commits to the attacker's repository instead of the intended one, i.e., the exact "silent corruption of what the user commits or pushes" impact class. Because Desktop performs the push using the signed-in account's stored token against whatever host/URL is now configured, this can also result in the user's authenticated push credentials being exercised against attacker infrastructure the user never approved.

### Likelihood Explanation
This requires no local/physical access, no malware, and no unnatural user steps: it only requires (1) the tracked upstream repository to be renamed, deleted, or transferred (a normal GitHub event the user does not control), and (2) an attacker claiming the freed `owner/name` slot before/when Desktop performs its routine background re-sync (`repositoryWithRefreshedGitHubRepository`, triggered e.g. on account-change refresh and other periodic paths in `app-store.ts`). The check that partially exists (`remoteUrlUnchanged` / `urlsMatch`) prevents overriding a manually-configured custom remote, but does nothing to prevent trusting a same-name-but-different repository returned by the API — it only distinguishes "did the user manually retarget" from "did GitHub's answer change," not "is this actually still the same repository."

### Recommendation
Do not silently rewrite the remote URL based on an `owner/name` match alone. Either (a) require the underlying GitHub repository's stable numeric ID (already available via `GitHubRepository`/`apiRepo.id` in most flows) to match the previously stored ID before treating the API's `clone_url` as authoritative, or (b) surface an explicit user-facing confirmation before automatically changing `origin`'s URL, similar to how `RepositorySettings` requires the user to explicitly submit a new remote URL: [4](#0-3) 

### Proof of Concept
1. User clones and tracks `https://github.com/alice/project.git` in Desktop; Desktop stores `gitHubRepository.cloneURL` = that URL.
2. Alice renames/transfers/deletes `alice/project` on GitHub (a routine, attacker-uncontrolled or attacker-assisted event).
3. Attacker (or anyone) creates a new repository at the now-free `alice/project` slot (or a scenario where `owner` itself is later re-registered/renamed to reuse the slug), with malicious content and their own `clone_url`.
4. On the next background sync (e.g., triggered from `refreshSelectedRepositoryAfterAccountChange` / other calls to `withRefreshedGitHubRepository` in `app-store.ts`), `matchGitHubRepository` still parses `owner=alice, name=project` from the *local, unchanged* remote, `api.fetchRepository('alice','project')` now returns the attacker's repo, and `updateRemoteUrl` finds `protocolsMatch === true`, `remoteUrlUnchanged === true` (user never edited origin), `urlsMatch === false` (different underlying repo) → it calls `gitStore.setRemoteURL('origin', attackerCloneUrl)` with no user prompt.
5. The victim's next `git fetch`/`push` from Desktop silently operates against the attacker's repository.

This exact override logic and its test expectations are visible in the accompanying test file, which only asserts protocol/URL-equality behavior and does not test repository-identity (ID) stability across renames: [5](#0-4)

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

**File:** app/src/ui/repository-settings/repository-settings.tsx (L296-313)
```typescript
    if (this.state.remote && this.props.remote) {
      const trimmedUrl = this.state.remote.url.trim()

      if (trimmedUrl !== this.props.remote.url) {
        try {
          await this.props.dispatcher.setRemoteURL(
            this.props.repository,
            this.props.remote.name,
            trimmedUrl
          )
        } catch (e) {
          log.error(
            `RepositorySettings: unable to set remote URL at ${this.props.repository.path}`,
            e
          )
          errors.push(`Failed setting the remote URL: ${e}`)
        }
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
