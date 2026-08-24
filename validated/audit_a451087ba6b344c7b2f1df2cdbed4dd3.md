### Title
Silent, unconfirmed rewrite of a repository's `origin` remote URL from an unverified GitHub API response - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
GitHub Desktop periodically re-fetches the GitHub repository metadata associated with a local clone and, if the API's `clone_url` no longer matches the local `origin` remote, automatically rewrites the local git remote URL with no user confirmation. This mirrors the H-04 bug-class pattern of "an address/reference is updated but the security-relevant follow-up step (approval / verification) is skipped" — here the "address" is the git remote URL, and the missing step is any user confirmation or strict identity verification before repointing where the user's future commits/pushes go.

### Finding Description
`updateRemoteUrl` compares the currently cached `GitHubRepository.cloneURL` against the local `origin` remote and, if the remote hasn't been manually changed by the user and the URL protocol still matches, silently calls `gitStore.setRemoteURL()` to point `origin` at whatever `clone_url` the GitHub API now reports for that repository: [1](#0-0) 

This is invoked from the periodic repository refresh flow, `repositoryWithRefreshedGitHubRepository`, which fetches the repository from the API using the account/owner/name association and then calls `updateRemoteUrl` unconditionally on that response — no dialog, no diffing shown to the user: [2](#0-1) 

The actual mutation happens in `GitStore.setRemoteURL`, which runs `git remote set-url` directly against the on-disk repository: [3](#0-2) [4](#0-3) 

The only safety checks are: (1) the URL scheme/protocol must match, and (2) the current remote must still equal the previously cached `clone_url` (i.e., the user hasn't manually customized the remote): [5](#0-4) 

Neither check verifies that the *new* `clone_url` still refers to the same underlying repository identity the user originally trusted (e.g., same repository id, or same owner/name) — it only checks hostname/owner/name equality against the *old* cached value to see if anything changed at all (`urlsMatch`), and if it changed, it accepts the new value outright. Because the owner/name used to fetch the repository come from parsing the existing remote URL itself (`matchGitHubRepository`), a repository that has been renamed or transferred (while GitHub keeps serving requests to the old owner/name via its redirect/lookup mechanism) will cause the API to legitimately return a different `clone_url` — which Desktop then silently adopts as the new `origin`.

### Impact Explanation
If the upstream repository the user cloned is later renamed or transferred by whoever controls it (a scenario fully within an unprivileged remote-repository owner's control — not requiring any access to the victim's machine, credentials, or the Desktop app itself), the next background refresh will silently repoint the victim's local `origin` remote to the new location, with no notification. Any subsequent `git push` from the user goes to the new destination instead of the one the user originally reviewed and trusted. This is a silent corruption of "where the user's commits/pushes go," matching the class of issue called out as valid in the prompt (attacker controls a GitHub API object, and the result is silent corruption of what the user pushes) — no local access, admin rights, malware, leaked credentials, or social engineering is required.

### Likelihood Explanation
Medium. It requires only that: (1) the local repository has an associated GitHub repository (`gitHubRepository` set), (2) the remote hasn't been manually edited since last matched, and (3) the periodic repository refresh runs (which is routine, not user-initiated) after the upstream owner/name-to-clone_url mapping changes. All of these are common, unprivileged conditions; no special user action or confirmation is needed to trigger the rewrite.

### Recommendation
Do not silently rewrite the `origin` remote URL based solely on a changed `clone_url` from an unattended background refresh. At minimum:
- Surface a confirmation prompt/banner to the user before changing the remote URL (as is already done in the manual "Repository Settings" flow), rather than mutating it invisibly.
- Additionally verify a stable identifier (e.g., the repository's numeric GitHub `id`) has not changed between the previously cached and newly fetched `GitHubRepository`, and refuse/flag the auto-update if the identity changed unexpectedly (e.g., due to a repository being deleted and a new one created with the same owner/name, or an unexpected transfer).

### Proof of Concept
1. Victim clones a public repository `https://github.com/attacker/foo` in GitHub Desktop; Desktop associates it via `matchGitHubRepository` and caches `GitHubRepository.cloneURL`. [6](#0-5) 
2. The repository owner (attacker) renames/transfers `attacker/foo`, so that GitHub's API — when queried for `owner=attacker, name=foo` — still resolves (via GitHub's rename-redirect handling) but now returns a different `clone_url` (e.g., pointing at a different, attacker-controlled destination/organization the attacker also controls).
3. On the next automatic background sync, `repositoryWithRefreshedGitHubRepository` fetches the repo and passes the new `apiRepo` into `updateRemoteUrl`. [2](#0-1) 
4. Since the local remote still equals the old cached `cloneURL` and the protocol is unchanged, `updateRemoteUrl` calls `gitStore.setRemoteURL(...)`, silently rewriting `origin` on disk via `git remote set-url`. [7](#0-6) 
5. The victim, unaware of any change, later runs `git push` (or uses Desktop's push button) and pushes commits to the new, attacker-controlled destination instead of the repository they originally reviewed — with no warning shown at any point.

Note: I was not able to fully verify GitHub.com's exact current redirect/grace-period semantics for renamed/transferred repositories (i.e., how long the old `owner/name` continues to resolve via the API to a changed `clone_url`), since that depends on GitHub's live API behavior rather than anything in this repository's code. This affects the precise exploitation window but not the underlying code-level gap: the update path performs no user confirmation and no strong identity check before rewriting the remote.

### Citations

**File:** app/src/lib/stores/updates/update-remote-url.ts (L18-45)
```typescript
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

**File:** app/src/lib/stores/app-store.ts (L4886-4907)
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
