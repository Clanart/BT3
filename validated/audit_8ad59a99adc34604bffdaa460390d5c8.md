## Finding: GitHub API response can silently repoint a repository's git remote to attacker infrastructure

### Title
Unauthenticated GitHub/GHES API `clone_url` field silently overwrites the local git remote, redirecting pushes/fetches - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl` automatically rewrites a repository's git remote URL whenever the GitHub API's `clone_url` for the associated repo differs from the locally configured remote, on the assumption that this only happens after a legitimate rename/transfer. The function never verifies that the new host is the account's own endpoint or `github.com`, so an attacker who can influence the API response (a malicious/compromised GitHub Enterprise Server, or a MITM on the API/proxy path — an in-scope threat per the report) can point the value to an arbitrary URL. Desktop applies this change without any user confirmation, silently changing where the user's future commits are fetched from and pushed to. This is the structural analog of the Audius `deployerCut` bug: a single externally-supplied value governs a security-relevant outcome (reward distribution / push destination) and can be changed by an untrusted party right before the sensitive operation (`claimRewards` / `git push`), with no timelock, confirmation, or secondary validation to protect the victim.

### Finding Description
`updateRemoteUrl` is called from `repositoryWithRefreshedGitHubRepository` (`app/src/lib/stores/app-store.ts:4904-4907`) every time Desktop refreshes GitHub repository metadata (12 call sites of the wrapping `withRefreshedGitHubRepository`, invoked on checkout, branch switching, account-change refresh, etc.): [1](#0-0) 

The actual decision logic: [2](#0-1) 

The guard rails are:
- `protocolsMatch` — only checks that both URLs use the same scheme (`https` vs `https`, or both being non-parseable "ssh-like" strings).
- `remoteUrlUnchanged` — confirms the *previously cached* `gitHubRepository.cloneURL` still matches the *current* local remote (i.e., the user hasn't manually repointed the remote away from the GitHub association).
- `urlsMatch` — is false whenever the *new* `apiRepo.clone_url` differs from the current remote by hostname, owner, or name (`urlMatchesRemote`, `app/src/lib/repository-matching.ts:90-118`).

None of these checks constrain the new URL to the same host as the account/endpoint the repository is associated with, nor to `github.com`/the configured GHES host. If `apiRepo.clone_url` is `https://evil.example.com/attacker/repo`, all three conditions can be satisfied and `gitStore.setRemoteURL` is invoked, silently rewriting the on-disk `.git/config` remote: [3](#0-2) 

`apiRepo` is fetched via `API.fetchRepository(owner, name)` (`app/src/lib/api.ts:972-988`), an authenticated GitHub/GHES API call whose response body is fully attacker-controlled if the attacker sits on the network path (or controls a GHES/proxy endpoint) — squarely the "git remote/proxy response" attacker model called out as valid in the report.

### Impact Explanation
Once the remote is silently rewritten, every subsequent `git push`/`git fetch`/`git pull` for that repository targets the attacker's server instead of the user's real GitHub repository, with no dialog or confirmation shown to the user (contrast this with the explicit `UpstreamAlreadyExists` dialog Desktop shows for a related, less dangerous scenario — `app/src/ui/upstream-already-exists/upstream-already-exists.tsx`). This matches the report's valid-impact category of "silent corruption of what the user commits or pushes," since the user believes they are pushing to their real repository while their commits (and potentially credentials/tokens supplied by Desktop's credential helper/trampoline for that push) are instead sent to attacker-controlled infrastructure.

### Likelihood Explanation
The refresh path (`repositoryWithRefreshedGitHubRepository`) is exercised routinely during normal use — after checkouts, branch switches, and account refreshes — so a single successfully-tampered API response is enough to persist the malicious remote. The check that's supposed to prevent unwanted rewrites (`remoteUrlUnchanged`/`urlsMatch`) only protects against Desktop clobbering a remote the user *manually* changed; it does nothing to validate the new value's trustworthiness, so it does not stop this path.

### Recommendation
Restrict automatic remote-URL updates to hosts matching the associated account's endpoint (or a small allow-list derived from it), and/or require explicit user confirmation (similar to `UpstreamAlreadyExists`) before any auto-managed remote URL change is applied — particularly whenever the new URL's hostname differs from the currently configured remote's hostname.

### Proof of Concept
1. User has an account added to Desktop for a GHES instance (or the attacker MITMs/compromises a proxy in the request path to `api.github.com`).
2. Desktop calls `api.fetchRepository(owner, name)` as part of a routine refresh (e.g., triggered by checking out a branch).
3. The attacker-controlled response returns `clone_url: "https://attacker.example.com/attacker/repo"`.
4. `updateRemoteUrl` computes `protocolsMatch = true`, `remoteUrlUnchanged = true` (local remote still matches the last known good clone URL), `urlsMatch = false` (hostnames differ) and calls `gitStore.setRemoteURL(...)`.
5. The repository's `origin` remote in `.git/config` is now `https://attacker.example.com/attacker/repo` with no dialog shown.
6. The next time the user clicks "Push," their commits are sent to the attacker's server. [4](#0-3)

### Citations

**File:** app/src/lib/stores/app-store.ts (L4890-4907)
```typescript
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
