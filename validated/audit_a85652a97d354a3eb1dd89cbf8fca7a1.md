## Title
Silent origin-remote hijack via GitHub API `clone_url` trusted over a stale local comparison - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl` automatically rewrites the local `origin` remote URL whenever the GitHub API's `clone_url` for a repository differs from the currently configured remote, gated only by a check that the *previously cached* `gitHubRepository.cloneURL` still equals the current remote. This mirrors the Radiant `_checkPoolsWithBalanceAreIncluded` bug-class: a security-relevant decision is made by comparing a fresh, attacker-influenceable value against a stale cached value, and if that comparison is satisfied, a state-changing action (here, silently repointing where the user's future pushes/fetches/credentials go) is performed with no user confirmation and no additional identity check. [1](#0-0) 

### Finding Description
`repositoryWithRefreshedGitHubRepository` re-fetches the GitHub API repository object for a locally tracked repo and, if that succeeds, calls `updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)`: [2](#0-1) 

Inside `updateRemoteUrl`, the only gates before silently changing the on-disk `git remote set-url` are:
1. `protocolsMatch` — the scheme (https/ssh) hasn't changed.
2. `remoteUrlUnchanged` — the *current* remote URL still matches the repository's **cached** `gitHubRepository.cloneURL` (i.e., the user hasn't manually edited the remote since the last time Desktop cached this value).
3. `!urlsMatch` — the new API-provided `clone_url` differs from the current remote.

If all three hold, Desktop calls `gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)` — no dialog, no confirmation, no verification that the new URL is still owned by a trusted account: [3](#0-2) 

The broken invariant is the same shape as the Radiant bug: the check is performed against a *previously accepted, cached* value (`gitHubRepository.cloneURL`) rather than against a freshly re-validated trust anchor, and the actual state-changing action (rewriting the remote a user's next `git push`/`git fetch`/credential helper will target) is taken automatically as soon as that stale-vs-fresh comparison lines up. Anyone who can influence what the GitHub API returns for `clone_url` on a subsequent request — e.g., a repository-takeover/"repojacking" scenario where a renamed/deleted repo's name is reclaimed by an attacker, an org/repo transfer, or (for GitHub Enterprise users) a malicious/compromised enterprise API endpoint or MITM proxy returning a crafted API response — can cause Desktop to repoint the user's remote to an attacker-controlled clone URL the next time the app performs its periodic GitHub-repository refresh. Existing guards (`protocolsMatch`, `remoteUrlUnchanged`, `urlMatchesRemote`) only check *syntactic* consistency between cached/local/remote strings; none of them verify that the new URL is still associated with an account the user trusts, and none require user confirmation, unlike the analogous "Update remote" flow surfaced for fork-upstream mismatches in `UpstreamAlreadyExists` which *does* prompt the user before mutating a remote: [4](#0-3) 

### Impact Explanation
If exploited, all future `git push`/`git fetch` operations for that remote silently target the attacker's repository/server. Combined with Desktop's credential helper wiring, this can exfiltrate the victim's push credentials/tokens to the attacker-controlled endpoint and/or cause the victim to unknowingly push commits to (or pull malicious content from) a repository the attacker controls — i.e., silent corruption of what the user pushes and a credential-exfiltration vector, which matches the "Valid Impact" criteria (attacker controls a GitHub API object; result is credential exfiltration or silent corruption of what the user pushes/fetches).

### Likelihood Explanation
This requires a specific precondition: the local remote's URL must still match the cache (`remoteUrlUnchanged`), and the attacker must control a `clone_url` returned by the GitHub API for a repository name the app already associates with this local repo (e.g., via repo renaming/deletion+reclaim, an org transfer to an attacker, or a compromised/malicious GHE endpoint). This is a real, non-trivial, but plausible remote-side attacker-controlled-object scenario (no local/physical access, no malware, no leaked credentials required), triggered automatically during Desktop's routine background repository refresh rather than requiring unusual user action.

### Recommendation
- Do not silently rewrite the remote URL based solely on a stale-cache comparison; require the new `clone_url`'s host/owner/id to be verified against an authoritative identity (e.g., the GitHub repository's numeric `id`/`node_id`, not just string URL matching) before treating it as "the same repository."
- Surface a confirmation dialog (as already exists for the fork-upstream-mismatch case in `UpstreamAlreadyExists`) any time Desktop proposes to change a remote URL as a side effect of an API refresh, rather than applying it automatically.
- Additionally invalidate/require re-confirmation when a previously known repository's ownership metadata (owner login, repository id) changes between refreshes, since a `clone_url` change combined with an ownership change is a strong repojacking signal.

### Proof of Concept
1. User has a repository cloned locally with `origin` pointing at `https://github.com/victim-org/some-repo.git`, tracked with a `GitHubRepository` whose cached `cloneURL` is the same.
2. The upstream repository `victim-org/some-repo` is deleted or transferred, and an attacker registers/creates a repository under a name that the GitHub API subsequently returns as the `clone_url` for the same `owner/name` lookup Desktop uses (or, for a GHES/self-hosted deployment, a compromised or MITM'd API endpoint returns a crafted `clone_url` for the existing repository object) — e.g. `https://attacker.example.com/evil/some-repo.git`.
3. On its next periodic refresh, Desktop calls `repositoryWithRefreshedGitHubRepository`, which fetches the (now attacker-influenced) `apiRepo.clone_url` and passes it into `updateRemoteUrl`.
4. Because `remoteUrlUnchanged` is still true (user hasn't touched the remote) and `protocolsMatch` holds, `urlsMatch` is false, so `gitStore.setRemoteURL('origin', 'https://attacker.example.com/evil/some-repo.git')` executes silently with no user prompt.
5. The user's next `git push`/`git fetch` from Desktop now targets the attacker's server, exfiltrating push credentials/tokens and/or letting the attacker serve malicious content as if it were the original repo.

Note: I was not able to fully trace every call path that triggers `repositoryWithRefreshedGitHubRepository` (e.g., exact refresh cadence/triggers) within the indexed content; a Devin session with full repository access would be needed to confirm the precise automatic-refresh triggers and any additional guards that may exist elsewhere in `app-store.ts`.

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

**File:** app/src/ui/upstream-already-exists/upstream-already-exists.tsx (L27-46)
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
```
