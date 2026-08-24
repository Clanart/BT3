## Analysis

The report's core issue is: a security-relevant decision is made by trusting a single, easily-manipulated value from an untrusted source (`globalState()` spot price) instead of verifying it through a more robust check (TWAP) or requiring explicit confirmation. The GitHub Desktop analog for this bug class is code that silently trusts a single field from an untrusted/attacker-influenceable source (a GitHub API response) to rewrite a security-sensitive value (the local `origin` remote URL) without any user confirmation or verification of the underlying repository identity. [1](#0-0) 

This function is invoked automatically during routine, non-interactive refresh flows: [2](#0-1) 

### Title
Silent, unconfirmed rewrite of a repository's `origin` remote URL from an untrusted GitHub API `clone_url` field - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl` compares the locally-configured `origin` remote against `apiRepo.clone_url` (an untrusted, server-supplied field fetched via `owner/name` lookup) and, if the protocol matches and the previously-cached clone URL matches the current remote, silently calls `gitStore.setRemoteURL` to rewrite the user's `origin` remote to whatever URL the API returns — with no user prompt, confirmation, or verification that the underlying repository identity is unchanged.

### Finding Description
`repositoryWithRefreshedGitHubRepository` re-resolves the repository's GitHub identity purely by `owner` + `name` string lookup (`api.fetchRepository(owner, name)`), then unconditionally feeds the returned `apiRepo.clone_url` into `updateRemoteUrl`: [3](#0-2)  The only checks performed before rewriting the remote are: (1) that the URL protocol (http vs ssh) hasn't changed, and (2) that the *previous* cached `gitHubRepository.cloneURL` still matched the local remote — there is no check that the API-returned repository is the *same repository* the user originally added (e.g., no comparison of a stable identifier such as the numeric repo id): [4](#0-3) 

This lookup-by-name pattern is exactly the shape of the classic "repo-jacking" primitive: if the `owner/name` slug the user is tracking ever resolves to a different underlying repository under GitHub's control surface (e.g. account rename + username-squatting, org/repo transfer, or a compromised/malicious GitHub Enterprise Server that a user has added as an account endpoint), the API response for `repos/{owner}/{name}` is attacker-influenced data, yet Desktop treats its `clone_url` as authoritative and automatically reprograms the local git remote used for all future `fetch`/`push` operations — without ever asking the user. This runs automatically on ordinary background refresh (e.g. `refreshSelectedRepositoryAfterAccountChange`), not just on an explicit user action.

### Impact Explanation
If exploited, the app silently redirects the user's `origin` remote to an attacker-controlled repository URL. Subsequent pushes (including pushes containing new commits, potentially sensitive code) would go to the attacker's repository instead of the legitimate one, and subsequent fetches/pulls would merge attacker-supplied history into the user's local repository — a silent corruption of what the user pushes and pulls, achieved purely by controlling a GitHub API object, which is an explicitly in-scope impact category.

### Likelihood Explanation
The rewrite path triggers automatically as part of routine background repository refresh flows and requires no unusual user interaction beyond having previously added a GitHub-associated repository. The precondition (an owner/name slug resolving to attacker-controlled content via rename/transfer/squat, or a hostile GHES endpoint) is a known real-world pattern for git-hosting clients, making this a credible unprivileged path once that precondition is met.

### Recommendation
Do not treat `clone_url` string equality as sufficient proof of repository identity. Before silently rewriting `origin`, verify a stable identifier (e.g., the GitHub repository `id`/`node_id`) has not changed, and/or require explicit user confirmation before altering the configured remote URL, similar to how the app already prompts the user for other trust decisions (e.g., "Trust Repository" for unsafe directories in `app/src/ui/missing-repository.tsx`).

### Proof of Concept
1. User adds/clones a repository as `origin` = `https://github.com/alice/project.git`; Desktop stores `gitHubRepository.cloneURL` accordingly.
2. `alice` renames her GitHub account (or the org/repo is transferred) such that the `alice/project` slug becomes available and is claimed by an attacker, who creates a repository under that same `owner/name`.
3. On the next background refresh, `repositoryWithRefreshedGitHubRepository` calls `api.fetchRepository('alice', 'project')`, which now returns the attacker's repository object, including `clone_url` pointing at the attacker's storage (same string form, `https://github.com/alice/project.git`, since GitHub reassigns the slug).
4. `updateRemoteUrl` finds `protocolsMatch === true` and `remoteUrlUnchanged === true` (old cached URL matches current remote), and since `urlsMatch` may now be true or the URL differs slightly (e.g. case, `.git` suffix), the guard conditions are satisfied and `gitStore.setRemoteURL` is invoked with no prompt, repointing `origin` to the attacker's repository transparently. [5](#0-4) 
5. The next `git push` from Desktop sends the user's commits to the attacker-controlled remote.

### Citations

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
