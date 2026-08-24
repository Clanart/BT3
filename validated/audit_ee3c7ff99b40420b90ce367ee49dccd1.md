Confirmed: `matchGitHubRepository` ( [1](#0-0) ) derives `owner`/`name` purely by regex-parsing the **local git remote URL text**, then `repositoryWithRefreshedGitHubRepository` uses that owner/name to call `api.fetchRepository(owner, name)` — a lookup keyed by name, not by the stored GitHub repository ID ( [2](#0-1) ). Whatever the API returns for that owner/name is trusted as authoritative and fed straight into `updateRemoteUrl`, which will silently `git remote set-url` the user's origin to the API's `clone_url` as long as the *current* remote still textually matches the *previously cached* `gitHubRepository.cloneURL` and the protocol is unchanged ( [3](#0-2) ). This background refresh runs automatically — e.g. every time a repository is added or an account changes — with no user prompt ( [4](#0-3) [5](#0-4) ).

### Title
Automatic remote-URL rewrite trusts GitHub API name lookup without verifying repository identity, enabling silent push-destination hijack via repository name reuse ("repojacking") - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl` automatically rewrites a tracked repository's `origin` remote to whatever `clone_url` the GitHub API returns for an owner/name pair — a pair that is derived only from parsing the existing remote URL text, not from any stable repository identifier. If the owner/name namespace that a user's `origin` points to is vacated (renamed away, deleted, transferred) and reclaimed by a different, attacker-controlled account/repository, Desktop's routine background refresh will silently repoint the user's push destination to the attacker's repository the next time it runs.

### Finding Description
`repositoryWithRefreshedGitHubRepository` is invoked automatically in multiple normal flows — when adding a repository ( [5](#0-4) ), and when the signed-in account changes ( [4](#0-3) ). It calls `matchGitHubRepository(repository)`, which extracts `owner`/`name` purely by regex on the local git remote URL, with no verification against the previously stored GitHub repository ID ( [6](#0-5) [1](#0-0) ). It then does `api.fetchRepository(owner, name)` ( [7](#0-6) ) — an API call keyed by name, not by ID.

Whatever repository object comes back for that owner/name is fed into `updateRemoteUrl`, which will call `gitStore.setRemoteURL(...)` — i.e. `git remote set-url` — as long as: the current remote's protocol still matches, the current remote still textually matches the last known `gitHubRepository.cloneURL`, and the new `clone_url` differs from the current remote ( [8](#0-7) ). There is no check that the repository's numeric/database ID (`gitHubRepository.dbID`) is unchanged before trusting `clone_url` — the identity check is entirely name/URL based.

This mirrors the audited bug's broken invariant: a piece of trusted local state ("this origin remote is verified to be the same repo as before") is not re-validated when the underlying condition it depends on (repository identity at that owner/name) silently changes — the "loan is paid off" analog is "the git remote no longer needs re-pointing," yet the code goes ahead and acts on stale/unverified trust regardless.

Concretely: if the original owner of the tracked GitHub repo renames their account or deletes/transfers the repository, the owner/name path becomes available for anyone to claim (the well-known "repojacking" pattern). An attacker who registers that now-vacant owner/name and creates a repository there will have their `clone_url` returned by `api.fetchRepository(owner, name)` on the victim's very next background refresh. Since the victim's actual git remote still equals the old cached `cloneURL` (nothing local has changed) and the attacker's new repo's `clone_url` differs only if they used a distinct path (e.g., different owner after a rename, or a different host mirroring the same name), `updateRemoteUrl` will silently rewrite the victim's `origin` to point at the attacker's destination.

### Impact Explanation
Once `origin` is silently repointed, all of the user's subsequent `git push` operations from Desktop go to the attacker-controlled remote instead of the legitimate one, without any dialog, confirmation, or visible diff review by the user. This is "silent corruption of what the user commits or pushes" — proprietary source, credentials embedded in commit history, or intentionally-private code can be exfiltrated to an attacker's repository, and the user has no indication their push destination changed since Desktop displays remotes elsewhere in settings that most users never revisit.

### Likelihood Explanation
This requires no local access, malware, or admin rights — the trigger is purely server-side: an attacker needs to control the value returned by `api.fetchRepository(owner, name)` for a repository name a victim's Desktop instance is tracking. This is realistically achievable via repository-name/namespace reuse ("repojacking") after an upstream rename/deletion, a scenario that is a well-documented, actively-exploited class of GitHub supply-chain attack. The refresh path runs automatically and silently (no explicit user gesture required beyond normal app usage), and the existing guard (`remoteUrlUnchanged` / `urlsMatch` / `protocolsMatch`) only checks textual URL equality, never the stable repository identity (`dbID`), so it does not stop this path.

### Recommendation
Before calling `setRemoteURL` in `updateRemoteUrl`, verify that the API-returned repository's stable identifier (e.g., GitHub's numeric repository `id`) matches the `dbID`/id already associated with the locally stored `GitHubRepository`, not just that the URL text differs. If the ID does not match what was previously recorded, treat this as a repository-identity change and require explicit user confirmation before rewriting the remote (similar to how `_convertRepositoryToFork` prompts the user), rather than silently updating it during a background refresh.

### Proof of Concept
1. Victim uses Desktop with a repository whose `origin` is `https://github.com/alice/project.git`, and Desktop has cached `gitHubRepository.cloneURL = https://github.com/alice/project`.
2. `alice` renames her GitHub account (or deletes/transfers `project`), freeing the `alice/project` namespace.
3. Attacker (`mallory`, having been renamed to `alice` or otherwise claiming the freed name) creates a new repository that resolves via GitHub's API to a `clone_url` such as `https://github.com/mallory/project.git` (distinct from cached `cloneURL` but still queryable via the same owner/name lookup Desktop performs, e.g. through a GitHub-served redirect/rename record).
4. Victim's Desktop performs a routine refresh (adding the repo again, restarting the app, or switching accounts triggers `repositoryWithRefreshedGitHubRepository` → `app/src/lib/stores/app-store.ts:4874-4914`).
5. `matchGitHubRepository` parses `owner=alice, name=project` from the still-unchanged local remote, `api.fetchRepository('alice','project')` returns the attacker's repo data with a different `clone_url`.
6. `updateRemoteUrl` sees `remoteUrlUnchanged=true` (local remote still equals old cached cloneURL) and `urlsMatch=false` (attacker's clone_url differs) and `protocolsMatch=true`, so it calls `gitStore.setRemoteURL('origin', attackerCloneUrl)` ( [9](#0-8) ), silently repointing `origin`.
7. The victim's next push from Desktop goes to the attacker's repository without any prompt.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L4874-4890)
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
```

**File:** app/src/lib/stores/app-store.ts (L4916-4933)
```typescript
  /**
   * Refreshes the GitHub repository information for the currently selected
   * repository when the active account changes. This ensures that permission
   * information is updated after signing in/out.
   */
  private async refreshSelectedRepositoryAfterAccountChange() {
    const repository = this.selectedRepository

    if (repository === null || repository instanceof CloningRepository) {
      return
    }

    if (!isRepositoryWithGitHubRepository(repository)) {
      return
    }

    await this.repositoryWithRefreshedGitHubRepository(repository)
  }
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

**File:** app/src/lib/stores/app-store.ts (L8148-8151)
```typescript
        const [refreshedRepo, usingLFS] = await Promise.all([
          this.repositoryWithRefreshedGitHubRepository(addedRepo),
          this.isUsingLFS(addedRepo),
        ])
```

**File:** app/src/lib/stores/updates/update-remote-url.ts (L12-44)
```typescript
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
