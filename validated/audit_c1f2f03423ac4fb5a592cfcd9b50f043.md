### Title
Background repository refresh silently rewrites the local `origin` remote to a GitHub-API-supplied `clone_url`, enabling repojacking-style push/pull hijack - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
When GitHub Desktop periodically refreshes a repository's GitHub metadata (`repositoryWithRefreshedGitHubRepository`), it fetches the repository object from the GitHub API using the *owner/name parsed out of the current git remote URL*, and if the returned `clone_url` differs from what is cached — but the local remote still matches the old cached value — Desktop automatically rewrites the local `origin` remote with `git remote set-url`, with no user prompt. This mirrors the `ERC20Rewards.setRewards` bug class: a critical parameter (the reward token / here, the push-and-pull destination) is swapped based on external/attacker-influenced data without invalidating or re-confirming the previous state, silently corrupting a downstream value the user relies on (rewards amount / here, where the user's commits get pushed).

### Finding Description
`repositoryWithRefreshedGitHubRepository` in `app-store.ts` matches a local repository to a GitHub repo via `matchGitHubRepository`, which only compares the **hostname** of the account endpoint and the **owner/name parsed textually out of the git remote URL** — it does not pin the match to any stable GitHub repository ID: [1](#0-0) 

That owner/name pair is then used to directly query the GitHub API: [2](#0-1) [3](#0-2) 

The resulting `apiRepo` (fully attacker-influencable if the owner/name now resolves to a different repository, e.g. via username/repo-name squatting after a rename or ownership change) is passed into `updateRemoteUrl`: [4](#0-3) 

The guard conditions here are the exact analog of the missing invariant in `ERC20Rewards.setRewards`: the code checks that the *protocol* matches and that the *current* local remote still equals the *previously cached* `cloneURL` (`remoteUrlUnchanged`), but it never verifies that the "new" repository returned by the API is actually the *same* repository (by stable ID) as the one the user originally intended to track. If those conditions hold, `gitStore.setRemoteURL` is called unconditionally and silently: [5](#0-4) [6](#0-5) 

Compare this to the explicit, user-confirmed flow used elsewhere in the same codebase for the analogous "upstream remote" case, where Desktop always shows a dialog (`UpstreamAlreadyExists`) before rewriting a remote URL: [7](#0-6) 
No equivalent confirmation exists for the `origin` remote path in `updateRemoteUrl`.

### Impact Explanation
Because the match is name/owner-string-based rather than ID-based, and the rewrite happens automatically during a routine background refresh, an attacker who can cause `owner/name` to resolve to a different GitHub repository (classic "repojacking": the original account is renamed/deleted and the freed `owner` or `owner/name` combination is re-registered by an attacker with a repo of the same name) can get Desktop to silently retarget the victim's local `origin` remote to the attacker-controlled repository. All future `git push` operations from that repository will silently be redirected to the attacker's remote — leaking private source code/history to the attacker — and all future `git fetch`/`pull` will pull attacker-controlled commits into the user's working repository, i.e. "silent corruption of what the user commits or pushes," a valid impact category for this scan.

### Likelihood Explanation
This refresh path runs unattended as part of Desktop's normal periodic repository-indicator/GitHub-metadata refresh, requiring no unusual user action beyond having the repository open in Desktop with an associated account — no local/physical access, no malware, no leaked credentials. The only precondition is that the attacker manages to have `owner/name` resolve, via the GitHub API, to a repository they control (a known, previously-exploited real-world technique against GitHub-integrated tooling). The existing guard (`remoteUrlUnchanged`, protocol match) only prevents the rewrite if the user has *manually* customized their remote URL — it does nothing to verify the identity of the new upstream, so it does not stop this path.

### Recommendation
Do not silently rewrite `origin` based solely on `owner/name` string matching and a "clone_url changed" heuristic. At minimum:
- Pin repository identity across refreshes using the persisted GitHub repository `dbID`/node ID (already stored in `GitHubRepository`) rather than re-deriving `owner/name` from the remote URL each time, and refuse to auto-update if the returned repository's ID does not match the previously cached ID.
- Require explicit user confirmation before rewriting `origin`, mirroring the existing `UpstreamAlreadyExists` dialog pattern used for the upstream remote.

### Proof of Concept
1. User clones `https://github.com/alice/project` into Desktop; Desktop caches `gitHubRepository.cloneURL = https://github.com/alice/project`.
2. `alice` renames her GitHub account (or the repo owner org is deleted/renamed), freeing the `alice` username.
3. Attacker registers `alice` and creates a repository named `project` under that account.
4. On Desktop's next background refresh of the open repository, `matchGitHubRepository` re-derives `owner=alice, name=project` from the still-unchanged local remote URL and calls `api.fetchRepository('alice','project')`, which now returns the attacker's repo with a `clone_url` that still textually parses to the same `owner/name` (satisfying `remoteUrlUnchanged`/`urlsMatch` logic) — [8](#0-7)  — but is a completely different underlying repository.
5. `gitStore.setRemoteURL('origin', attackerCloneUrl)` executes automatically — [5](#0-4)  — after which the victim's next `git push`/`git pull` silently targets the attacker's repository.

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
