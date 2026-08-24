### Title
Silent, unconfirmed rewrite of a repository's git remote URL based on trust in a GitHub/GHES API response - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
The external report's underlying invariant break is: a security-relevant configuration value (`priceFeeds`) is trusted at set-time and never re-validated or gated by any control before being used for critical operations. The Desktop analog is `updateRemoteUrl()`, which automatically rewrites the local git repository's `origin` remote URL using the `clone_url` field returned by a call to the configured GitHub/GHE/GHES API endpoint — without any user confirmation, diffing beyond a coarse string/protocol comparison, or revalidation against the account that the user actually intended to trust.

### Finding Description
`updateRemoteUrl` is invoked from `repositoryWithRefreshedGitHubRepository` in `app/src/lib/stores/app-store.ts` as part of routine, backgroundish repository refresh: [1](#0-0) 

It fetches `apiRepo` from the API for the account/owner/name resolved via `matchGitHubRepository` (hostname-based matching only) [2](#0-1) , and then calls `updateRemoteUrl`, which decides to overwrite the local `origin` remote with `apiRepo.clone_url` if the protocol scheme string matches and the *previous* clone URL (from the last cached `GitHubRepository` record) still matches the current remote: [3](#0-2) 

The only checks performed are: (1) whether the URL scheme (`http:`/`https:`) is unchanged, and (2) whether the remote hasn't been "manually" changed since the last time Desktop cached the GitHub repository's clone URL. There is no verification that the *host* returned in `clone_url` corresponds to the account/endpoint the user is signed into, no confirmation prompt, and no re-validation step comparable to "changing price feeds" safely (e.g. governance/allow-list/owner confirmation). The value driving this rewrite — the API response body — originates from whatever server answers requests to the configured endpoint, which for GitHub Enterprise Server users is a self-hosted, network-reachable host, i.e., attacker-influenceable via a compromised/malicious GHES instance or a MITM proxy sitting in front of it (explicitly an allowed attacker model: "a git remote/proxy response").

### Impact Explanation
If a GHES server (or a network position able to tamper with responses to it) returns a manipulated `clone_url` for the matched `owner/name` repository, Desktop will silently rewrite the user's `origin` remote to point at an attacker-chosen URL, as long as the URL scheme superficially matches. Because this happens automatically during a routine refresh with no user-facing confirmation dialog, all subsequent `git fetch`/`git pull` operations will silently retrieve code from — and `git push` operations will silently send commits, branches, and credentials-bearing traffic to — the attacker-controlled remote. This is precisely the "silent corruption of what the user commits or pushes" impact category: the user believes they are interacting with their trusted repository while Desktop has redirected the underlying git remote without their knowledge.

### Likelihood Explanation
The refresh path (`repositoryWithRefreshedGitHubRepository`) runs as normal application behavior whenever GitHub repository info is refreshed, not behind any privileged action, and does not require the user to take any unusual step — it is triggered by ordinary use of Desktop against a GHES account. The only precondition is that the attacker can influence the API response for the matched `owner/name` (feasible for a malicious/compromised GHES instance or an on-path proxy responding to that endpoint), which fits squarely within the stated valid-impact model. No local access, admin rights, or pre-existing malware is required.

### Recommendation
Do not use API-provided `clone_url` values to silently mutate an already-configured git remote. At minimum:
- Require explicit user confirmation before rewriting an existing remote URL as a result of an API response.
- Validate that the new URL's hostname matches the hostname of the account's configured endpoint before considering an update, not just that the URL scheme is unchanged.
- Treat `updateRemoteUrl`'s "remote hasn't been manually changed" heuristic as insufficient trust evidence, since it only prevents *repeated* rewrites, not a *first* malicious rewrite.

### Proof of Concept
1. Sign into a GitHub Enterprise Server account in Desktop pointing at a server the attacker controls or can MITM (e.g., a compromised internal GHES instance, or a network position intercepting traffic to it).
2. Add/clone a repository whose remote matches that account's hostname (satisfying `matchGitHubRepository`).
3. When Desktop performs its periodic `repositoryWithRefreshedGitHubRepository` refresh, have the attacker-controlled server respond to the `repos/{owner}/{name}` API call with a `clone_url` pointing at an attacker-controlled git host but using the same URL scheme (`https://`) as the original.
4. Observe that `updateRemoteUrl` rewrites the local `origin` remote to the attacker's URL with no prompt, per the logic at `app/src/lib/stores/updates/update-remote-url.ts:42-44`.
5. Subsequent `git push` from the user silently uploads commits to the attacker's repository instead of the intended one.

Note: I was not able to fully trace every downstream consumer of `getRemotes`/`setRemoteURL` in `app/src/lib/stores/git-store.ts` (file was truncated in the index at time of review) to confirm there is no additional confirmation layer added elsewhere in the push/fetch UI flow; if such a confirmation step exists, the report's severity should be revised downward accordingly.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4887-4907)
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
