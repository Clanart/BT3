## Analysis

The Solana report's broken invariant is: **an identity established at init time (mint address) is later re-trusted for a mutable property (decimals) without pinning it to a stable, unforgeable value**, allowing an attacker-controlled object to change shape between checks. The closest structural analog in GitHub Desktop is `updateRemoteUrl`, which re-trusts the *identity* of a GitHub repository across API refreshes using only text-matching of owner/name parsed out of a URL, never a stable repository ID — then uses that unverified match to silently rewrite the user's local git `origin` remote.

### Title
Silent auto-rewrite of the local git remote URL based on unauthenticated GitHub API `clone_url`, matched only by owner/name string parsing - (File: app/src/lib/stores/updates/update-remote-url.ts)

### Summary
`repositoryWithRefreshedGitHubRepository` in `app/src/lib/stores/app-store.ts` periodically re-resolves a repository's GitHub identity by parsing the current git remote's URL for an `owner/name` pair via `matchGitHubRepository`, fetching that repo from the GitHub API, and then calling `updateRemoteUrl` to overwrite the local `origin` remote if the API's `clone_url` differs from what's on disk. [1](#0-0) [2](#0-1) 

### Finding Description
`GitHubRepository` never records the GitHub API's immutable numeric repository id — only a purely local `dbID` used for the app's own database: [3](#0-2) 

Because there is no stable identity anchor, `matchGitHubRepository` re-derives "which GitHub repo this is" purely by regex-parsing the *current* git remote URL's owner/name and matching the hostname against a configured account: [4](#0-3) 

`updateRemoteUrl` then decides whether it's safe to autonomously rewrite the on-disk remote based only on two checks: (1) the current remote still textually matches the previously cached `gitHubRepository.cloneURL`, and (2) the URL protocol hasn't changed. If both hold, and the freshly-fetched `apiRepo.clone_url` differs from the current remote, it silently calls `gitStore.setRemoteURL` to overwrite the remote — with no re-verification that the API object refers to the same underlying repository (no repo id, node_id, or SSH host key pinning is checked): [5](#0-4) 

This is the same class of bug as the Solana report: a value that should be pinned once at trust-establishment time (repository identity) is instead re-derived from a mutable, attacker-influenceable input (a URL string / an API response object) on every refresh cycle, and that re-derived value is used to make a security-relevant write (rewriting the git remote that all future `fetch`/`pull`/`push`/credential-helper operations will target) without any additional authorization check.

### Impact Explanation
If the periodic GitHub API response for `fetchRepository(owner, name)` can be influenced — e.g. a compromised/malicious GitHub Enterprise Server endpoint the user has added an account for, or a network path capable of tampering with that specific HTTPS response (the "attacker controls...a git remote/proxy response" case) — the attacker can return an arbitrary `clone_url`. Desktop will then automatically rewrite the user's `origin` remote to that attacker-chosen URL with no user prompt, no diff shown, and no confirmation dialog. All subsequent `git fetch/pull/push` operations, along with any embedded or credential-manager-cached credentials sent during those operations, are silently redirected to the attacker's host, which corresponds to "silent corruption of what the user commits or pushes" / "credential exfiltration" in the accepted impact categories.

### Likelihood Explanation
This refresh path runs automatically in the background (`repositoryWithRefreshedGitHubRepository` is invoked as part of routine repository refresh flows in `app-store.ts`), requiring no user action beyond having the repository open in Desktop with a configured account pointed at the attacker-influenced endpoint. The only "guard" — `protocolsMatch && remoteUrlUnchanged && !urlsMatch` — checks string equality of URLs, not cryptographic or ID-based identity, so it does not stop a spoofed API response from a controlled/malicious endpoint.

### Recommendation
- Persist the GitHub API's immutable repository `id`/`node_id` in `GitHubRepository` at the time the association is first made, and require that `apiRepo.id` match the previously stored id before treating a `clone_url` change as a legitimate rename.
- Never silently auto-write to the git remote configuration; instead surface a confirmation prompt to the user showing old vs. new remote URL before calling `setRemoteURL`.

### Proof of Concept
1. User adds a GitHub Enterprise Server account in Desktop and opens a repository whose `origin` remote matches that GHE instance's `clone_url` exactly (satisfying `remoteUrlUnchanged`).
2. The GHE endpoint (attacker-controlled, or a MITM position on that specific request) responds to `GET /repos/{owner}/{name}` with a `clone_url` pointing to an attacker-controlled git host, same protocol scheme (e.g., `https://`). [6](#0-5) 
3. On the next automatic refresh (`repositoryWithRefreshedGitHubRepository`), `updateRemoteUrl` sees `protocolsMatch = true`, `remoteUrlUnchanged = true`, `urlsMatch = false`, and calls `gitStore.setRemoteURL(...)`, rewriting `origin` to the attacker's URL without any prompt. [7](#0-6) 
4. The next `git push`/`git pull`/`git fetch` from Desktop targets the attacker's host, potentially exfiltrating pushed commits or credentials sent by the credential helper.

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

**File:** app/src/models/github-repository.ts (L15-23)
```typescript
  public constructor(
    public readonly name: string,
    public readonly owner: Owner,
    /**
     * The ID of the repository in the app's local database. This is no relation
     * to the API ID.
     */
    public readonly dbID: number,
    public readonly isPrivate: boolean | null = null,
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

**File:** app/src/lib/stores/updates/update-remote-url.ts (L7-44)
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
```
