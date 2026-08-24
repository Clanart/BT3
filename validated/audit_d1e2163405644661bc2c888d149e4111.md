Based on the investigation, the closest analog I found in this codebase is the automatic, unconfirmed remote-URL rewrite performed by `updateRemoteUrl`, which mutates a security-relevant value (the git push/fetch destination) based on untrusted, externally-supplied data, without any user confirmation — echoing the M02 pattern of a "guarded" write path silently mutating a sensitive value under attacker-influenceable conditions.

### Title
Silent, unconfirmed rewrite of a repository's `origin` remote URL from GitHub API data - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl` automatically calls `gitStore.setRemoteURL` to change a repository's tracked remote URL whenever the GitHub API's `clone_url` for the associated repository differs from what's on disk, as long as three heuristic conditions are met, with no user prompt or confirmation dialog. [1](#0-0) 

### Finding Description
The function only guards against changing the remote when the protocol differs or when the user has manually edited the remote away from what Desktop previously recorded as the GitHub `cloneURL`: [2](#0-1) 

The equality check that gates the update, `remoteUrlUnchanged`, is computed via `urlMatchesRemote`, which only compares hostname, owner login, and repo name (case-insensitively) — not a stable repository ID: [3](#0-2) 

`apiRepo` originates directly from a GitHub API response fetched during a background repository refresh flow, `repositoryWithRefreshedGitHubRepository`, which calls `api.fetchRepository(owner, name)` and then feeds the result into `updateRemoteUrl`: [4](#0-3) 

Because the match between the local remote and the tracked `GitHubRepository` is keyed off owner/name (from `matchGitHubRepository`, which parses the *existing remote URL* to find owner/name, not a persistent GitHub numeric ID), and because the update path fires automatically as part of routine background refresh without any dialog, an API response whose `clone_url` differs from the current remote — while still passing the loose "unchanged" check — will cause Desktop to silently repoint `origin` to a new URL. This mirrors the M02 class of bug: a state-mutating function has a narrow set of guard conditions that were designed for a benign case (repo renamed by its real owner) but do not verify a stable identity invariant, so the mutation can be triggered under attacker-influenceable conditions without the safeguard (user confirmation) that a security-sensitive change like this should have. [5](#0-4) 

### Impact Explanation
If an attacker can influence the `clone_url` returned by the GitHub API for a tracked repository (e.g., a compromised/malicious GitHub Enterprise server, a repository-transfer/rename scenario engineered by an attacker who gains temporary control of the org/repo, or a name/owner collision exploited via `matchGitHubRepository`'s name-based matching), Desktop will silently rewrite the user's `origin` remote without any prompt. Subsequent `git fetch`/`pull` operations would silently pull from the attacker-controlled remote (supply-chain risk: malicious commits merged into the user's working copy), and subsequent `git push` operations would silently push the user's code/credentials-adjacent context to the attacker's repository (data exfiltration of private source code). This falls squarely within the "git remote/proxy response... silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
This requires the attacker to control or manipulate the GitHub API response for a repository that Desktop is already tracking (e.g., via a rename/ownership-transfer chain, or a compromised Enterprise API endpoint), and it depends on the "protocol match" and "remote unchanged" heuristics happening to pass. This is a real, code-supported path (not requiring local access, admin rights, or social engineering) but the exact exploitation chain for github.com itself is constrained by GitHub's normal redirect/rename semantics; it's more directly reachable against GitHub Enterprise deployments or MITM'd API traffic. I could not fully verify all call sites and background-refresh cadence in the time available, so I can't state with certainty how frequently/automatically this runs without additional investigation.

### Recommendation
Gate `setRemoteURL` in `updateRemoteUrl` behind an explicit stable-identity check (e.g., compare the GitHub repository's numeric `id`, not just owner/name string matching) and require user confirmation (similar to the existing `UpstreamAlreadyExists` dialog pattern already used elsewhere in the codebase) before silently changing `origin`'s URL, rather than performing the mutation unattended during background refresh. [6](#0-5) 

### Proof of Concept
1. User adds/clones a repository that Desktop associates with GitHub repo `owner/repo` (matched purely by owner+name via `matchGitHubRepository`).
2. Through a rename, ownership transfer, or a compromised/GHE API endpoint, the API response for that same `owner/repo` slug later returns a different `clone_url` pointing to an attacker-controlled repository, while the previous stored `cloneURL` still matches the current remote (satisfying `remoteUrlUnchanged`) and the protocol is unchanged (satisfying `protocolsMatch`).
3. On the next background refresh (`repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl`), Desktop calls `gitStore.setRemoteURL(...)` with the attacker's URL with no dialog shown to the user.
4. The user's next `git fetch`/`push` silently interacts with the attacker's repository instead of the intended one. [7](#0-6)

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

**File:** app/src/ui/upstream-already-exists/upstream-already-exists.tsx (L23-41)
```typescript
/**
 * The dialog shown when a repository is a fork but its upstream remote doesn't
 * point to the parent repository.
 */
export class UpstreamAlreadyExists extends React.Component<IUpstreamAlreadyExistsProps> {
  public render() {
    const name = this.props.repository.name
    const gitHubRepository = forceUnwrap(
      'A repository must have a GitHub repository to add an upstream remote',
      this.props.repository.gitHubRepository
    )
    const parent = forceUnwrap(
      'A repository must have a parent repository to add an upstream remote',
      gitHubRepository.parent
    )
    const parentName = parent.fullName
    const existingURL = this.props.existingRemote.url
    const replacementURL = parent.cloneURL
    return (
```
