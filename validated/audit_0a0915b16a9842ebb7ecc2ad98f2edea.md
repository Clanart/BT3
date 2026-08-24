### Title
Silent, automatic rewriting of a Git remote URL from an unverified GitHub API repository object - (`File: app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
GitHub Desktop periodically "refreshes" the `GitHubRepository` metadata attached to a local repository by re-querying the GitHub API for the *same owner/name pair* that was originally matched, and — if certain conditions hold — automatically rewrites the local Git remote's URL to whatever `clone_url` that API call returns, without asking the user for confirmation. [1](#0-0) [2](#0-1) 

### Finding Description
`repositoryWithRefreshedGitHubRepository` re-derives the GitHub identity of a tracked repository via `matchGitHubRepository`, which is a pure owner/login + repo-name string match against the account's endpoint hostname — it never keys off GitHub's immutable numeric repository `id`: [3](#0-2) 

That `{owner, name}` pair is then used to call `api.fetchRepository(owner, name)`. Whatever repository object the API server returns for that `owner/name` route is trusted and fed into `updateRemoteUrl`: [4](#0-3) 

`updateRemoteUrl` then compares the *current* local remote URL to what was previously cached as the GitHub repository's `cloneURL`. If the local remote hasn't been manually changed since the last known association (`remoteUrlUnchanged`) and the protocol (http/https/ssh-scp-like) still matches, it silently calls `gitStore.setRemoteURL(...)`, rewriting `origin` (or whichever remote) to the new `clone_url` — with no user prompt, diff, or confirmation dialog: [5](#0-4) 

This mirrors the exact broken-invariant pattern in the referenced report: a dependent component (`Quest`) trusted an *address* that was captured at one point in time from a mutable authority (`QuestFactory.rabbitholeReceiptContract`), and that authority was later free to point elsewhere without the dependent being told. Here, the "authority" is the GitHub API response for a given `owner/name` string, and the "dependent" is the locally persisted Git remote. Nothing in `matchGitHubRepository`/`updateRemoteUrl` verifies that the repository the API now returns for that `owner/name` is the *same* repository object (by stable ID) the user originally cloned/associated. If the original repository at that name is deleted (owner account renamed/deleted, org repo deleted, or repository transferred/renamed away) and a different, attacker-controlled repository is created and later renamed into that same now-vacant `owner/name` slot, the background refresh (`repositoryWithRefreshedGitHubRepository`, invoked from routine repository refresh paths in `app-store.ts`) will fetch the attacker's repository, decide the local remote is still "unchanged" relative to the previous association, and silently repoint the user's local `origin` remote at the attacker's `clone_url`.

Existing guards do not stop this path:
- `remoteUrlUnchanged` only checks that the user hasn't *manually* edited the remote — it does nothing to verify the *target* repository's continuity/identity.
- `protocolsMatch` only prevents an http⇄ssh protocol downgrade; an attacker-controlled repo on the exact same host/protocol passes trivially.
- `urlMatchesRemote`/`urlsMatch` compare hostname+owner+name strings only, so a same-named replacement repository is indistinguishable from the legitimate one. [6](#0-5) 

### Impact Explanation
Once the local `origin` remote is silently repointed, all subsequent `fetch`, `pull`, and `push` operations target the attacker's repository transparently through the normal push/fetch/checkout code paths, which just take `remote.url` at face value: [7](#0-6) [8](#0-7) 

Because the credential trampoline resolves credentials by matching the *account's endpoint origin* against the remote host (same hostname, e.g. `github.com`), the user's existing GitHub token will be transparently supplied to Git operations against the attacker's repository if it lives on the same GitHub host: [9](#0-8) 

This can result in: silent exfiltration of the user's pushed commits/content to an attacker-controlled repository, silent corruption of what the user believes they are pushing to (their real project), and pulling/merging attacker-supplied history into the user's working tree without any warning that the remote target changed.

### Likelihood Explanation
The precondition (original `owner/name` becoming available for reuse — e.g., account/org deletion+recreation, repository deletion, or a rename race) is a known, previously-exploited class of "repo/account-jacking" on GitHub and does not require any local/admin access, leaked credentials, or social engineering of the victim beyond them having previously used Desktop with that repository. The refresh path (`repositoryWithRefreshedGitHubRepository`) runs as part of Desktop's routine background repository refresh, so it triggers automatically without any unusual user action.

### Recommendation
- Persist and compare GitHub's immutable numeric repository `id` (already available in `IAPIRepository`/`IAPIFullRepository`) rather than trusting `owner/name` string continuity when deciding whether an API-fetched repository is "the same" one previously associated.
- In `updateRemoteUrl`, refuse to auto-rewrite the remote URL if the newly fetched repository's `id` differs from the previously stored `GitHubRepository`'s id; instead, surface a prompt to the user before changing anything.
- Treat any repository-identity change (id mismatch) as requiring explicit re-association, mirroring how `_convertRepositoryToFork` explicitly re-associates only via an interactive UI flow rather than an automatic background refresh.

### Proof of Concept
1. Attacker creates `evil-org/target-repo` on the same GitHub host that user U has as `origin` for their local repository associated with `original-org/target-repo` (previously deleted/renamed, freeing the name).
2. Wait for the original `owner/name` slot (e.g. `original-org/target-repo`) to become available, and register a new repository there under attacker's control, or arrange for the original repository to be renamed away and reused.
3. User U's Desktop instance runs its routine background refresh, calling `repositoryWithRefreshedGitHubRepository`, which calls `matchGitHubRepository` (owner/name string match only) and `api.fetchRepository(owner, name)`, returning the attacker's repository object. [10](#0-9) 
4. Because the user's local remote URL still equals the previously cached `cloneURL` for `original-org/target-repo` (`remoteUrlUnchanged === true`) and protocol matches, `updateRemoteUrl` calls `gitStore.setRemoteURL(...)`, silently rewriting `origin` to the attacker's `clone_url`. [11](#0-10) 
5. User's next `git push`/`git pull` (via `push.ts`/`fetch.ts`) transparently operates against the attacker's repository, using the trampoline-resolved GitHub credential for the same host. [9](#0-8)

### Citations

**File:** app/src/lib/stores/app-store.ts (L4874-4909)
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

**File:** app/src/lib/repository-matching.ts (L28-46)
```typescript
/** Try to use the list of users and a remote URL to guess a GitHub repository. */
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

**File:** app/src/lib/git/push.ts (L48-61)
```typescript
export async function push(
  repository: Repository,
  remote: IRemote,
  localBranch: string,
  remoteBranch: string | null,
  tagsToPush: ReadonlyArray<string> | null,
  options?: PushOptions,
  progressCallback?: (progress: IPushProgress) => void
): Promise<void> {
  const args = [
    'push',
    remote.name,
    remoteBranch ? `${localBranch}:${remoteBranch}` : localBranch,
  ]
```

**File:** app/src/lib/git/fetch.ts (L39-48)
```typescript
export async function fetch(
  repository: Repository,
  remote: IRemote,
  progressCallback?: (progress: IFetchProgress) => void,
  isBackgroundTask = false
): Promise<void> {
  let opts: IGitStringExecutionOptions = {
    successExitCodes: new Set([0]),
    env: await envForRemoteOperation(remote.url),
  }
```

**File:** app/src/lib/trampoline/find-account.ts (L20-29)
```typescript
export async function findGitHubTrampolineAccount(
  accountsStore: AccountsStore,
  remoteUrl: string
): Promise<Account | undefined> {
  const accounts = await accountsStore.getAll()
  const parsedUrl = new URL(remoteUrl)
  return accounts.find(
    a => new URL(getHTMLURL(a.endpoint)).origin === parsedUrl.origin
  )
}
```
