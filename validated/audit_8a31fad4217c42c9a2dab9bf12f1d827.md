## Title
`updateRemoteUrl` silently rewrites the local `origin` remote based on unauthenticated trust of the GitHub API `clone_url` field, without confirming the target repository identity - (`app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
The external report's core defect is: a security-relevant identity field (`address`) is deserialized from an untrusted/attacker-influenceable source and then used to route funds without any re-validation or user confirmation, because the code assumes the loaded value is trustworthy just because it came from the expected storage location. The direct analog in GitHub Desktop is `updateRemoteUrl` in `app/src/lib/stores/updates/update-remote-url.ts`, which takes the `clone_url` field from a `GitHub API` repository object and, if a handful of loose heuristic conditions are met, silently calls `gitStore.setRemoteURL` to rewrite the user's local `origin` remote — the value that determines where the next `git push`/`git fetch` actually goes — without ever showing the user what changed or asking for confirmation.

### Finding Description
`repositoryWithRefreshedGitHubRepository` in `app/src/lib/stores/app-store.ts` periodically re-fetches repository metadata from the GitHub API using the owner/name pair last known to Desktop and, if the request succeeds, calls `updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)`: [1](#0-0) 

Inside `updateRemoteUrl`, the API-provided `apiRepo.clone_url` is trusted as the new "correct" remote URL. The only guards are: (1) the URL's protocol (http vs ssh) hasn't changed, (2) the *previous* API-known clone URL still matches what's currently configured as `origin` (i.e., the user "hasn't manually changed it"), and (3) the new URL differs from the current one. None of these guards verify that the new `clone_url` still points to the *same underlying repository* — they only compare protocol and owner/name strings parsed out of URLs: [2](#0-1) 

If those three loose conditions hold, Desktop calls `gitStore.setRemoteURL(...)`, which shells out to `git remote set-url` and overwrites the address the user will push to and pull from, with no dialog, no confirmation, no diff shown to the user: [3](#0-2) 

The equality checks used throughout this code path (`urlMatchesRemote`, `urlsMatch`) are purely syntactic — they parse hostname/owner/name out of two URL strings and compare them as strings; they never confirm identity via a stable API resource id: [4](#0-3) [5](#0-4) 

This mirrors the report's broken invariant exactly: a value obtained from a file/API that is nominally "the same object" is deserialized and consumed as ground truth for a security-critical routing decision, without the code ever re-deriving or re-verifying that identity from a trustworthy primary source (in the Parity case, the wallet's cryptographic key; in this case, a stable repository identifier rather than a mutable owner/name pair looked up by string).

### Impact Explanation
If an attacker can cause the GitHub API to return a `clone_url` for the owner/name pair Desktop still has cached (for example, via a classic repository-name-squatting/"repojacking" scenario after the original repo is renamed or its owner account is renamed/deleted, or via a compromised/malicious account with push access to the same repo who transfers/renames it and lets an attacker claim the freed name), Desktop's background refresh will silently repoint the victim's local `origin` remote to the attacker-controlled repository URL. Subsequent `git push` operations performed by the user (who has no reason to suspect their remote changed) would then send commits/credentials-bearing HTTPS requests to the attacker's endpoint instead of the legitimate one — analogous to the "funds sent to the wrong, attacker-controlled address" outcome in the original report.

### Likelihood Explanation
This requires the attacker to control what the GitHub API returns for the specific owner/name Desktop has cached, which is a real-world reachable primitive (repo/account renames combined with name squatting are a well-documented supply-chain technique), not local/physical access, admin rights, or leaked credentials. It also requires no unusual user action — the refresh (`repositoryWithRefreshedGitHubRepository`) runs automatically as part of normal background repository refresh flows.

### Recommendation
Do not trust `apiRepo.clone_url` as sufficient grounds to silently rewrite a configured remote. Before calling `setRemoteURL`, verify repository identity using a stable, non-guessable API identifier (e.g., the repository's numeric/node `id`) captured at the time the `GitHubRepository` was first associated, and refuse to auto-update if that id doesn't match the id in the newly fetched `apiRepo`. At minimum, surface the change to the user (e.g., a notification or confirmation prompt showing old vs. new remote URL) rather than applying `git remote set-url` unattended.

### Proof of Concept
1. User clones `https://github.com/victim-org/some-repo` and Desktop tracks it as a `GitHubRepository` with `owner=victim-org`, `name=some-repo`.
2. `victim-org` renames `some-repo` to `some-repo-v2` (or the org/user is renamed/deleted), freeing up the `victim-org/some-repo` slug.
3. An attacker creates a new repository at the now-available `victim-org/some-repo` (or an attacker who controls a GHES-hosted equivalent to `victim-org`) with `clone_url` pointing to their own hosting.
4. Desktop's periodic `repositoryWithRefreshedGitHubRepository` calls `api.fetchRepository('victim-org', 'some-repo')` [6](#0-5)  which now resolves to the attacker's repo and returns its `clone_url`.
5. `updateRemoteUrl` sees `protocolsMatch` (both `https`), `remoteUrlUnchanged` (user never manually touched `origin`), and `!urlsMatch` is false in this exact case since owner/name strings are identical — so the "safety" check actually does nothing to prevent using a same-owner/name-but-different-actual-repository target, because identity is never verified beyond the string match. `gitStore.setRemoteURL('origin', <attacker clone_url>)` is executed silently. [7](#0-6) 
6. The user's next push/fetch talks to the attacker-controlled endpoint without any warning shown in the UI.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4886-4910)
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

    const ghRepo = await repoStore.upsertGitHubRepository(endpoint, apiRepo)
    const freshRepo = await repoStore.setGitHubRepository(repository, ghRepo)
```

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

**File:** app/src/lib/git/remote.ts (L56-64)
```typescript
/** Changes the URL for the remote that matches the given name  */
export async function setRemoteURL(
  repository: Repository,
  name: string,
  url: string
): Promise<true> {
  await git(['remote', 'set-url', name, url], repository.path, 'setRemoteURL')
  return true
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

**File:** app/src/lib/repository-matching.ts (L137-148)
```typescript
export function urlsMatch(url1: string, url2: string) {
  const firstIdentifier = parseRepositoryIdentifier(url1)
  const secondIdentifier = parseRepositoryIdentifier(url2)

  return (
    firstIdentifier !== null &&
    secondIdentifier !== null &&
    firstIdentifier.hostname === secondIdentifier.hostname &&
    firstIdentifier.owner === secondIdentifier.owner &&
    firstIdentifier.name === secondIdentifier.name
  )
}
```
