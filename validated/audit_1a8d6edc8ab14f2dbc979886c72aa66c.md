## Title
Silent, unconfirmed remote-URL rewrite based on name/owner-only GitHub API matching allows repo-jacking of push/fetch targets - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
The Sherlock report's broken invariant is: *an authorization/trust artifact (ERC20/721 approval) is bound to a mutable identity (club token) instead of the actual owner, so after the identity changes hands the old grantee can still act on it.* The GitHub Desktop analog is: Desktop re-derives "which GitHub repository does my `origin` remote belong to" purely from the **owner/name pair parsed out of the current remote URL**, not from a stable repository id, and then uses whatever the GitHub API currently returns for that owner/name to **automatically overwrite the local git remote URL** with no user confirmation. If the owner/name later refers to a different, attacker-controlled repository (rename-away + name squatting, or a compromised/forked identity reusing the same slug), Desktop will silently retarget the user's push/fetch remote to that attacker repository.

### Finding Description
`matchGitHubRepository` matches a remote purely by hostname + owner/name parsed from the URL string, with no repository id pinning: [1](#0-0) 

This match feeds `repositoryWithRefreshedGitHubRepository`, which fetches `api.fetchRepository(owner, name)` for whatever owner/name currently resolves from the stored remote, and — if a `gitHubRepository` is already associated — calls `updateRemoteUrl` to reconcile the local remote with the API response: [2](#0-1) 

`updateRemoteUrl` performs the actual rewrite. It only refuses to update when the protocol differs or when the user manually changed the remote away from the previously known `cloneURL`; otherwise, if the API-reported `clone_url` no longer matches the current remote, it calls `gitStore.setRemoteURL` with **no user prompt at all**: [3](#0-2) 

Contrast this with the only other remote-rewrite path in the app — the fork-upstream mismatch flow — which explicitly surfaces a confirmation dialog (`UpstreamAlreadyExists`) before ever touching the remote: [4](#0-3) 

There is also an acknowledged design gap: once a `GitHubRepository` association is set, Desktop "currently never clear[s] GitHub repository associations" (`app-store.ts` TODO referencing desktop/desktop#1144), so stale identity/permission data (fork parent, permissions, private/public flag) keeps flowing forward across refreshes instead of being invalidated when the underlying repository identity changes: [5](#0-4) 

Because none of this logic ever re-validates the GitHub repository's stable numeric id against what was previously associated, "the repository behind `owner/name`" is treated as invariant, and an owner/name is silently trusted the moment it can be resolved by the API, mirroring the Sherlock bug's failure to re-validate the "owner" of a mutable resource before honoring persisted trust.

### Impact Explanation
If a user's tracked repository's owner/name slug is later reused by a different, attacker-controlled repository on the same host (e.g. the legitimate repo is renamed/transferred and the old `owner/name` combination is reclaimed/squatted by an attacker, or an org repo is deleted and the name re-registered), the next automatic repository refresh will:
1. Fetch the attacker's repository object via `fetchRepository(owner, name)`.
2. Silently call `gitStore.setRemoteURL` to repoint `origin` to the attacker's `clone_url`, with zero user confirmation (`updateRemoteUrl`).
3. Persist the attacker's repository as the app's trusted `GitHubRepository` record via `upsertGitHubRepository`/`setGitHubRepository`, carrying forward incorrect permissions/fork/branch-protection state.

Once the remote is repointed, subsequent fetches/pulls silently pull attacker-controlled history/commits into the user's working repo, and subsequent pushes silently send the user's code/credentials-bearing history to the attacker's repository — i.e., silent corruption of what the user fetches and pushes, without any visible warning distinguishing it from a legitimate rename-following feature.

### Likelihood Explanation
This requires no local access, no malware, and no leaked credentials — only that the attacker can cause `owner/name` to resolve to a different repository than the one the user originally added (repo rename-and-squat, or org/user handle reuse), which is entirely within GitHub's normal, attacker-reachable object model (a "GitHub API object" the attacker controls, as scoped by the task). Desktop performs this reconciliation automatically as part of routine repository refresh flows, without requiring any unnatural user interaction, unlike the fork-upstream path which correctly gates the equivalent action behind `UpstreamAlreadyExists` confirmation.

### Recommendation
Pin the local `GitHubRepository` association to the API's stable repository `id`, not just owner/name derived from the remote URL. Before calling `setRemoteURL`, verify the fetched `apiRepo.id` matches the previously stored id for that association (or, for first-time association, require explicit user confirmation). Any owner/name mismatch against a previously known id should surface an explicit warning dialog (as already exists for the fork-upstream case) rather than silently rewriting the push/fetch target.

### Proof of Concept
1. User adds a repository whose `origin` remote is `https://github.com/victim-org/project.git`; Desktop stores it as a `GitHubRepository` with that `cloneURL` (`app/src/models/github-repository.ts`).
2. `victim-org/project` is renamed/transferred away (owner/name freed) and an attacker creates a new repository at the exact same `owner/name` slug.
3. On the next background refresh, `repositoryWithRefreshedGitHubRepository` re-derives `owner`/`name` from the still-unchanged local remote URL via `matchGitHubRepository`, calls `api.fetchRepository(owner, name)`, and now receives the **attacker's** `IAPIFullRepository` (same owner/name, different underlying repo/clone_url or same clone_url if attacker precisely squats the slug).
4. `updateRemoteUrl` sees `remoteUrlUnchanged` (matches previous stored `cloneURL`) and `protocolsMatch`, and — if the API `clone_url` differs at all (e.g. attacker later changes it, or a redirect/case difference occurs) — calls `gitStore.setRemoteURL(...)` with no prompt, repointing `origin` to the attacker-controlled repository.
5. The user's next `git fetch`/`git pull` retrieves attacker-supplied history, and the next `git push` sends the user's code to the attacker's repository, with the app UI still labeling it under the previously trusted context.

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

**File:** app/src/ui/upstream-already-exists/upstream-already-exists.tsx (L23-47)
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
      <Dialog
        title={
          __DARWIN__ ? 'Upstream Already Exists' : 'Upstream already exists'
        }
        onDismissed={this.props.onDismissed}
        onSubmit={this.onUpdate}
```
