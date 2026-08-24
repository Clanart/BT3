### Title
Silent, hostname-unvalidated rewrite of the trusted git remote from an untrusted GitHub API `clone_url` field - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl` mirrors the `setTranche`-class bug: it takes an externally supplied value (`apiRepo.clone_url`, a field returned by a GitHub/GHES API response) and writes it directly into a security-relevant slot (the repository's `origin` remote URL) via `gitStore.setRemoteURL`, while only performing a shallow, incomplete validation (protocol/scheme equality) instead of verifying the value is still the same trusted destination.

### Finding Description
`repositoryWithRefreshedGitHubRepository` derives `owner`/`name` from the repository's current git remote (`matchGitHubRepository`, `app/src/lib/repository-matching.ts:29-46`), then fetches `api.fetchRepository(owner, name)` against the account's configured API `endpoint` [1](#0-0) , and finally passes the raw API object into `updateRemoteUrl`: [2](#0-1) 

The only checks performed before overwriting the remote are:
- `protocolsMatch` — compares only the URL **scheme** (`https:` vs `ssh:`), not the hostname [3](#0-2) 
- `remoteUrlUnchanged` — compares the previously *cached* `gitHubRepository.cloneURL` (itself populated from a prior, equally-trusted API response) to the current remote [4](#0-3) 

Neither check confirms that the new `clone_url` still points at the same **host** as the account's endpoint or the previous remote. If both checks pass, Desktop calls `gitStore.setRemoteURL(...)` unconditionally with the API-supplied string — no user confirmation, no host allow-list, no re-validation against `account.endpoint`.

This is invoked automatically as part of routine repository refresh flows (e.g., on selecting a repository or after account changes), not behind any explicit user action, exactly like the unchecked `setTranche` setter in the original report: an address/value that “can be set” is written without validating that the new value is still consistent with the system invariant (same host/repository identity) it's supposed to represent.

### Impact Explanation
If a GitHub Enterprise Server account the user has added (a normal, expected trust relationship, not "prior malware" or "leaked credentials") is compromised or malicious, its API response for `repos/{owner}/{name}` can return an arbitrary `clone_url` using the same scheme (e.g., `https://attacker.example/owner/name.git`). Because `protocolsMatch` never checks hostname, Desktop will silently rewrite the user's `origin` remote to that attacker URL on the next background refresh. All subsequent `git fetch`/`git pull`/`git push` operations from that repository will silently be redirected to the attacker's host, resulting in:
- Silent corruption of what the user pushes (code is sent to an attacker-controlled destination instead of the intended one).
- Silent corruption of what the user pulls/checks out (subsequent fetches pull attacker-supplied history/content while the UI still shows the original repository name).

This matches the "silent corruption of what the user commits or pushes" impact category, driven by an attacker-controlled GitHub API object.

### Likelihood Explanation
Requires the user to have an account pointed at a compromised/malicious GHES-style endpoint (a scenario within the accepted "attacker controls...a GitHub API object" category, not requiring local access, admin rights, or leaked credentials). The refresh path (`repositoryWithRefreshedGitHubRepository`) runs automatically/periodically for tracked repositories, so no unusual user action beyond normal app usage is needed once the malicious endpoint is in play.

### Recommendation
In `updateRemoteUrl`, before calling `gitStore.setRemoteURL`, additionally validate that the hostname component of `updatedRemoteUrl` matches the hostname of the existing remote (or the account's expected endpoint), not just the scheme. Reject/ignore updates that change the host, and consider prompting the user for confirmation when the remote URL's host changes, rather than performing the rewrite silently.

### Proof of Concept
Not independently executable from the index alone (would require standing up or compromising a GHES-compatible endpoint account entry and exercising `repositoryWithRefreshedGitHubRepository`); conceptually:
1. Add an account pointing to a GHES-like `endpoint` that is attacker-controlled/compromised.
2. Have a tracked repository whose `origin` remote matches an `owner/name` on that endpoint.
3. Have the attacker's `/repos/{owner}/{name}` API response return `clone_url: "https://attacker.example/owner/name.git"` (same `https:` scheme).
4. Trigger a refresh (e.g., `repositoryWithRefreshedGitHubRepository`); `updateRemoteUrl` passes `protocolsMatch` (scheme-only) and `remoteUrlUnchanged`, and calls `gitStore.setRemoteURL('origin', 'https://attacker.example/owner/name.git')`, silently repointing all future git operations at the attacker host. [5](#0-4) [1](#0-0) [6](#0-5)

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

**File:** app/src/lib/stores/updates/update-remote-url.ts (L1-44)
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
