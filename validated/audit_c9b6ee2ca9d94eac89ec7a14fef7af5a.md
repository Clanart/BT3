## Title
Automatic, silent rewrite of a repository's local git remote URL from GitHub API metadata, without user confirmation, following a repository owner/name change - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl` silently runs `git remote set-url` on the user's `origin` remote whenever the app periodically refreshes GitHub metadata for a repository (via `repositoryWithRefreshedGitHubRepository` in `app-store.ts`) and finds that the GitHub API's `clone_url` for the associated repository no longer matches the locally configured remote. This mirrors the operator-not-reset bug class in the report: an old authorization/binding (the locally configured remote) silently continues to be trusted and is automatically re-pointed based on data supplied by a party (GitHub API / repo owner) that the user did not explicitly re-approve, without any UI confirmation that the destination of future pushes/fetches has changed.

### Finding Description
`repositoryWithRefreshedGitHubRepository` [1](#0-0)  is invoked as part of routine repository housekeeping (`withRefreshedGitHubRepository`, called from many git operation paths in `app-store.ts`). It looks up the account/owner/name for the repo via `matchGitHubRepository` [2](#0-1) , fetches fresh metadata from the API, and then calls `updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)` [3](#0-2) .

`updateRemoteUrl` itself decides — with no user prompt — to rewrite the local `origin` URL: [4](#0-3) 

The only guard rails are: (1) the protocol (`https`/`ssh`) must be unchanged, and (2) the *previous* cached `gitHubRepository.cloneURL` must still match the current remote (`remoteUrlUnchanged`), i.e. the user hasn't manually edited the remote. There is **no check that the new `apiRepo.clone_url` still refers to the same underlying repository identity** (GitHub's numeric repo id) — the comparison is purely done through `urlMatchesRemote`, which matches on hostname/owner/name strings [5](#0-4) . If a repository is renamed, transferred to a new owner, or a maintainer intentionally repoints an old name toward a completely different destination they now control, the GitHub API's `clone_url` for that repo record changes and Desktop will follow it automatically and rewrite the user's `.git/config`, exactly like the LimitOrderManager case where a stale `operator`/authorization silently continues to act after the underlying "owner" changed, because the invariant ("this remote points to the repo the user explicitly added") is never re-validated against a stable identity — only against loosely-matching strings supplied by the untrusted-relative-to-the-user API response.

### Impact Explanation
If exploited, this results in silent corruption of what the user pushes: their local `origin` is rewritten to point at a different repository URL than the one the user configured, without a dialog, confirmation, or even a highlighted diff in the UI. All subsequent `git push` (and `git fetch`) operations issued by Desktop use `gitStore.defaultRemote`/`currentRemote`, i.e., the rewritten URL, silently sending code and possibly credentials over the new destination. This falls within the accepted impact category "silent corruption of what the user commits or pushes," since the object being redirected (a GitHub API repository record returned in response to matching the user's existing remote by owner/name) is influenced by whoever controls that GitHub owner/name at fetch time — not by the account the user originally trusted when they cloned or added the remote.

### Likelihood Explanation
The refresh path runs automatically and repeatedly as part of normal background repository refreshes tied to routine git operations (`withRefreshedGitHubRepository` is called from numerous operations in `app-store.ts`), so no special user action beyond normal use of Desktop over time is required. The trigger condition (repo rename/transfer causing the API's `clone_url` to diverge from the configured remote while owner/name matching still resolves via `matchGitHubRepository`) is a legitimate, common GitHub feature (repository renames/transfers), making the precondition realistic without requiring any privileged or out-of-band access — it only requires that the entity controlling the previously-matched GitHub repository record changes.

### Recommendation
Do not derive trust for automatically rewriting the local git remote solely from a string match between the API's `clone_url` and the previous cached `cloneURL`. Instead, key the check on the stable GitHub repository identifier and require explicit user confirmation (a dialog, similar to existing "Repository moved/renamed" prompts) before mutating `.git/config`, rather than calling `gitStore.setRemoteURL` unconditionally inside `updateRemoteUrl`. This is analogous to the report's recommendation of invalidating the stale authorization when the underlying resource's ownership/binding changes, rather than continuing to trust it silently.

### Proof of Concept
1. User clones `https://github.com/alice/project.git` in GitHub Desktop and it is tracked with GitHub repository info (`cloneURL` = that URL).
2. `alice` transfers/renames the repository elsewhere (a legitimate GitHub action), and a different, now API-authoritative record for `alice/project` starts returning a different `clone_url` (e.g., because the name was subsequently reused or repointed by a new owner of that same owner/name pair).
3. On the next routine background refresh, Desktop calls `repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl`, sees `protocolsMatch && remoteUrlUnchanged && !urlsMatch`, and calls `gitStore.setRemoteURL(...)`, rewriting `origin` in `.git/config` to the new `clone_url` with no dialog shown to the user.
4. The user's next `git push`/`git fetch` in Desktop silently targets the new destination. [6](#0-5) [7](#0-6)

### Citations

**File:** app/src/lib/stores/app-store.ts (L4874-4913)
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
```

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

**File:** app/src/lib/repository-matching.ts (L90-118)
```typescript
export function urlMatchesRemote(url: string | null, remote: IRemote): boolean {
  if (url == null) {
    return false
  }

  const cloneUrl = parseRemote(url)
  const remoteUrl = parseRemote(remote.url)

  if (remoteUrl == null || cloneUrl == null) {
    return false
  }

  if (!caseInsensitiveEquals(remoteUrl.hostname, cloneUrl.hostname)) {
    return false
  }

  if (remoteUrl.owner == null || cloneUrl.owner == null) {
    return false
  }

  if (remoteUrl.name == null || cloneUrl.name == null) {
    return false
  }

  return (
    caseInsensitiveEquals(remoteUrl.owner, cloneUrl.owner) &&
    caseInsensitiveEquals(remoteUrl.name, cloneUrl.name)
  )
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
