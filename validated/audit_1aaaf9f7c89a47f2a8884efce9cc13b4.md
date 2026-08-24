### Title
Silent auto-rewrite of `origin` remote URL from unauthenticated GitHub API field, without user confirmation - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl()` compares the locally configured `origin` remote against the `clone_url` field returned by a `fetchRepository` API call, and — when a small set of heuristic conditions hold — calls `gitStore.setRemoteURL()` directly, silently rewriting the git remote that all future `git fetch`/`git push` operations use. Unlike the analogous "upstream remote differs" case, which shows the user the `UpstreamAlreadyExists` confirmation dialog before changing a remote, this path for the primary `origin` remote performs the change with no prompt at all.

### Finding Description
The function is invoked from `AppStore.repositoryWithRefreshedGitHubRepository`, on every full repository refresh (app foreground, selecting a repo, background fetch cycles, sign-in state changes), whenever the local repo has an associated `GitHubRepository`: [1](#0-0) 

`updateRemoteUrl` then does the actual write: [2](#0-1) 

The corrupted value is the `origin` remote's URL, held in git config (`.git/config`) and mirrored in `GitStore`'s in-memory state, and it is rewritten via `gitStore.setRemoteURL` → `setRemoteURL` (git plumbing `remote set-url`): [3](#0-2) [4](#0-3) 

The gating logic only checks: (1) URL protocol match between old and new remote, (2) that the currently configured remote still equals the *previously cached* `GitHubRepository.cloneURL` (i.e., the user hasn't manually customized it), and (3) that the URLs differ. None of these checks validate that the new `clone_url` value is trustworthy — it is taken verbatim from whatever the configured API endpoint (`account.endpoint`, which can be a self-hosted GitHub Enterprise Server or any endpoint the user has signed into) returns for `fetchRepository(owner, name)`. The task's "Valid Impact" scope explicitly includes "a git remote/proxy response" as an attacker-controlled input; a compromised/malicious GHES instance, a network path capable of tampering with API responses to that endpoint, or a compromised account with rename/transfer rights on the repo can all cause `apiRepo.clone_url` to point to an attacker-controlled git host.

This contrasts with the codebase's own established pattern for changing a *related* remote (`upstream`), where the same conceptual mismatch is deliberately surfaced to the user via a modal before any change is made: [5](#0-4) 

No equivalent confirmation exists for `origin`'s URL being silently swapped by `updateRemoteUrl`.

### Impact Explanation
Because the rewrite targets `origin` — the remote Desktop uses by default for fetch/pull/push — once redirected, every subsequent `git fetch`/`pull` performed from Desktop pulls content (including commits, refs, and potentially hooks/config depending on later actions) from the attacker-controlled host, and every subsequent `git push` sends the user's local commits and credentials-scoped requests there instead of the legitimate repository. This is "silent corruption of what the user commits or pushes" and can enable credential exfiltration during future push/fetch prompts, as well as supply-chain injection into the user's working tree on next fetch. The change happens with no dialog, no log visible to a normal user, and persists in `.git/config` beyond the app session.

### Likelihood Explanation
The trigger conditions (protocol match, remote URL previously matching the cached `cloneURL`, and a differing new `clone_url`) are the *common* case for an unmodified clone — most users never hand-edit their `origin` URL, so `remoteUrlUnchanged` is true by default. The refresh path (`repositoryWithRefreshedGitHubRepository`) runs routinely (on repo selection, background fetch, account changes), so exploitation doesn't require any unusual user action — it fires automatically as long as the attacker can get a single malicious `fetchRepository` response through to the client (e.g., controlling/compromising the configured Enterprise Server, or a MITM/compromised proxy on that endpoint, which is explicitly within scope per the prompt).

### Recommendation
Do not auto-apply `clone_url` changes to the `origin` remote without explicit user confirmation, mirroring the existing `UpstreamAlreadyExists` dialog pattern used for the upstream remote. At minimum, require that the change is only accepted from the specific endpoint/account the repository was originally associated with, log/notify the user prominently when the primary remote URL changes, and consider requiring the new host to match the previously known host (only permit path/name changes, not host changes) unless the user opts in.

### Proof of Concept
1. User has a repository cloned from `https://ghes.corp.example/org/repo` with `origin` pointing there, associated with a `GitHubRepository` whose cached `cloneURL` equals that same URL (`remoteUrlUnchanged` = true).
2. An attacker who can influence the response of `GET /repos/org/repo` from `ghes.corp.example` (compromised GHES instance, malicious reverse proxy in front of it, or an insider with rename/transfer rights) returns `clone_url: "https://ghes.corp.example/attacker/repo"` (same protocol/host, so `protocolsMatch` holds and `urlMatchesRemote` treats it as a mismatch vs. the current remote).
3. On the next automatic refresh, `AppStore._refreshRepository` → `repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl` runs; all three gating conditions are satisfied, so `gitStore.setRemoteURL('origin', 'https://ghes.corp.example/attacker/repo')` executes silently: [6](#0-5) 
4. The user, unaware of the change, performs a normal "Push" or "Fetch" from Desktop; the operation now targets `attacker/repo`, exfiltrating commits/credentials or serving malicious content on next fetch, with no dialog shown (unlike the `UpstreamAlreadyExists` flow that would have warned for an analogous upstream-remote scenario).

Note: I could not fully trace whether `AppStore.matchGitHubRepository`/`matchGitHubRepository` restricts matching to only accounts whose endpoint corresponds to a previously trusted signed-in account (this would partially mitigate purely network-based spoofing but not a compromised/malicious GHES/account scenario) — verifying this exactly would require a live Devin session with fuller code access.

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

**File:** app/src/ui/upstream-already-exists/upstream-already-exists.tsx (L27-47)
```typescript
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
