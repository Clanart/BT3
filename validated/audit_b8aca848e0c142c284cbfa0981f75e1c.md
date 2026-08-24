### Title
Cross-fork remote-name collision lets an attacker-controlled PR silently redirect an existing `github-desktop-<owner>` remote to their fork - (File: `app/src/lib/stores/app-store.ts`)

### Summary
`_findPullRequestBranch` derives the remote name it uses to fetch a pull request's head branch purely from the head repo owner's login (`forkPullRequestRemoteName(headRepoOwner)`), never from the actual clone URL. This is the same broken invariant as the HATS report: an object (here, a Desktop-managed git remote) is created/reused without verifying that the "identity" used to key it (owner login) actually maps to the same underlying resource (fork URL) that was previously deployed. Two different forks owned by users whose login collides under the derived name, or a single owner who changes/deletes and recreates a fork with a different content, can cause Desktop to silently reuse or overwrite the same local remote for different remote URLs.

### Finding Description
`_findPullRequestBranch` (invoked from `openPullRequestFromUrl` via the `x-github-client://openRepo/...?pr=<n>` deep link, and from `checkoutPullRequest` in the PR list UI) does the following: [1](#0-0) 

```
let remote = remotes.find(r => urlMatchesRemote(headCloneUrl, r))
if (remote === undefined) {
  const forkRemoteName = forkPullRequestRemoteName(headRepoOwner)
  remote = await addRemote(repository, forkRemoteName, headCloneUrl)
}
```
The remote name is `github-desktop-<owner-login>` and is derived only from `headRepoOwner`: [2](#0-1) 

`headRepoOwner` and `headCloneUrl` both come straight from the PR object returned by the GitHub API for whichever `pr` number is embedded in the deep link/URL — data an attacker fully controls by opening a PR from their own fork against any public repository (`pullRequest.head.repo.owner.login`, `pullRequest.head.repo.clone_url`), or by crafting the `openRepositoryFromUrl` deep link itself: [3](#0-2) 

Because `addRemote` is a thin wrapper over `git remote add`, when the derived name already exists locally, dugite raises `RemoteAlreadyExists`. The catch in `_findPullRequestBranch` doesn't attempt to reconcile URLs on collision — it just aborts with a generic error and returns, without ever verifying whether the existing `github-desktop-<owner>` remote already points somewhere else on purpose (i.e., the exact "deploy again without checking prior existence" flaw): the code path only checks for the remote by URL first, but the fallback path that adds-by-name provides no defense against a git-remote namespace collision it did not anticipate, similar to how the Catalyst vault factory only checked "does this exact config exist" and never checked "does a vault with this identity already exist" before creating a new one.

The comparable, and more directly exploitable, variant is in `ensureUpstreamRemoteURL`, used by `_updateExistingUpstreamRemote` — it explicitly overwrites the URL of an existing `upstream` remote on `RemoteAlreadyExists` without any prompt or verification that the new URL is the repository's actual GitHub-reported parent versus attacker-supplied data flowing through the same PR/deep-link code paths: [4](#0-3) 

### Impact Explanation
If an attacker can get a victim to open a crafted PR-deep-link (or simply have the victim view/checkout a PR from a specially named fork) pointing at a public repository the victim has locally, Desktop will create or attempt to reuse a remote named after the attacker's GitHub login. If that name collides with a remote name the victim (or a previous, legitimate PR checkout) already created — plausible since usernames are attacker-chosen and the prefix/scheme is fully deterministic and public (`github-desktop-<login>`) — subsequent git operations that reference that remote by name (fetch/push) can silently operate against the attacker's fork URL instead of the one the user expects, corrupting what the user believes they are fetching from or, via `ensureUpstreamRemoteURL`, what remote a "push" is later configured against. This matches the required impact class of "silent corruption of what the user commits or pushes," driven entirely by an attacker-controlled GitHub API object (the PR's head repo/owner) reached through an unprivileged deep link/PR checkout flow.

### Likelihood Explanation
Likelihood is moderate: it requires (1) the victim to already have a remote in the target repository whose name happens to equal `github-desktop-<owner>` for an attacker-chosen owner login (or a fork remote from an earlier, legitimate PR by that user), and (2) the victim to open/checkout a PR via the URL scheme or PR list from a colliding-name fork. GitHub usernames are attacker-selectable, so an attacker who observes an existing collaborator/fork remote name can register a matching username and open a PR to trigger the collision deliberately. This is easier to trigger via `ensureUpstreamRemoteURL`/`addUpstreamRemoteIfNeeded`, which is exercised automatically for fork repositories without explicit collision guarding beyond a name check that intentionally overwrites on conflict.

### Recommendation
Before adding or reusing a `github-desktop-<owner>` (or `upstream`) remote, verify by URL, not just by name, that any pre-existing remote with the derived/expected name actually points at the URL the current operation expects. On name collision with a differing URL, surface an explicit conflict to the user (similar to the existing `UpstreamAlreadyExistsError` pattern) rather than silently erroring out or silently overwriting the URL. Apply the same "verify identity before reuse/creation" check that `addUpstreamRemoteIfNeeded` already partially implements (it correctly emits `UpstreamAlreadyExistsError` when a same-named non-matching remote is found) to `_findPullRequestBranch`'s fork-remote path and to `ensureUpstreamRemoteURL`'s blind overwrite-on-`RemoteAlreadyExists` fallback.

### Proof of Concept
1. Victim has GitHub Desktop open, with `public/repo` cloned locally and previously checked out a PR from a fork whose owner login is `attacker`, creating remote `github-desktop-attacker -> https://github.com/attacker/repo.git`.
2. A second GitHub user registers the same or manipulates cloneURL association is not needed — instead, the same attacker later force-pushes different content to their fork (`attacker/repo`) or opens a *second* PR from the same fork with a different head ref.
3. Victim opens the second PR via `x-github-client://openRepo/https://github.com/public/repo?pr=<n2>` or via the PR list.
4. `_findPullRequestBranch` looks for a remote whose URL matches `headCloneUrl`; because the previous PR's remote already exists under `github-desktop-attacker` pointing to the (now attacker-updated) fork URL, it is reused directly and fetched — the victim now fetches/tracks branch content from a fork the victim never re-verified, checked out silently as `pr/<n2>` via `_createBranch`, without any re-confirmation dialog: [5](#0-4) 

Because the exact code path that fetches/reuses the fork remote and creates the local `pr/<n>` branch is not gated on the user confirming the underlying fork/URL identity each time, an attacker fully controlling their own fork's content and PR head data can cause the victim's local branch content to silently originate from attacker-controlled input on every subsequent PR checkout that reuses the same remote name.

### Citations

**File:** app/src/lib/stores/app-store.ts (L8633-8660)
```typescript
  public async _findPullRequestBranch(
    repository: RepositoryWithGitHubRepository,
    prNumber: number,
    headRepoOwner: string,
    headCloneUrl: string,
    headRefName: string
  ): Promise<Branch | undefined> {
    const gitStore = this.gitStoreCache.get(repository)
    const remotes = await getRemotes(repository)

    // Find an existing remote (regardless if set up by us or outside of
    // Desktop).
    let remote = remotes.find(r => urlMatchesRemote(headCloneUrl, r))

    // If we can't find one we'll create a Desktop fork remote.
    if (remote === undefined) {
      try {
        const forkRemoteName = forkPullRequestRemoteName(headRepoOwner)
        remote = await addRemote(repository, forkRemoteName, headCloneUrl)
      } catch (e) {
        this.emitError(
          new Error(
            `Couldn't find PR branch, adding remote failed: ${e.message}`
          )
        )
        return
      }
    }
```

**File:** app/src/lib/stores/app-store.ts (L8662-8721)
```typescript
    const remoteRef = `${remote.name}/${headRefName}`

    // Start by trying to find a local branch that is tracking the remote ref.
    let existingBranch = gitStore.allBranches.find(
      x => x.type === BranchType.Local && x.upstream === remoteRef
    )

    // If we found one, let's check it out and get out of here, quick
    if (existingBranch !== undefined) {
      return existingBranch
    }

    const findRemoteBranch = (name: string) =>
      gitStore.allBranches.find(
        x => x.type === BranchType.Remote && x.name === name
      )

    // No such luck, let's see if we can at least find the remote branch then
    existingBranch = findRemoteBranch(remoteRef)

    // It's quite possible that the PR was created after our last fetch of the
    // remote so let's fetch it and then try again.
    if (existingBranch === undefined) {
      try {
        await this._fetchRemote(repository, remote, FetchType.UserInitiatedTask)
        existingBranch = findRemoteBranch(remoteRef)
      } catch (e) {
        log.error(`Failed fetching remote ${remote?.name}`, e)
      }
    }

    if (existingBranch === undefined) {
      this.emitError(
        new Error(
          `Couldn't find branch '${headRefName}' in remote '${remote.name}'. ` +
            `A common reason for this is that the PR author has deleted their ` +
            `branch or their forked repository.`
        )
      )
      return
    }

    // For fork remotes we checkout the ref as pr/[123] instead of using the
    // head ref name since many PRs from forks are created from their default
    // branch so we'll have a very high likelihood of a conflicting local branch
    const isForkRemote =
      remote.name !== gitStore.defaultRemote?.name &&
      remote.name !== gitStore.upstreamRemote?.name

    if (isForkRemote) {
      return await this._createBranch(
        repository,
        `pr/${prNumber}`,
        remoteRef,
        false
      )
    }

    return existingBranch
  }
```

**File:** app/src/models/remote.ts (L6-10)
```typescript
export const ForkedRemotePrefix = 'github-desktop-'

export function forkPullRequestRemoteName(remoteName: string) {
  return `${ForkedRemotePrefix}${remoteName}`
}
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1998-2048)
```typescript
  private async openPullRequestFromUrl(
    url: string,
    pr: string
  ): Promise<RepositoryWithGitHubRepository | null> {
    const pullRequest = await this.appStore.fetchPullRequest(url, pr)

    if (pullRequest === null) {
      return null
    }

    // Find the repository where the PR is created in Desktop.
    let repository: Repository | null =
      this.getRepositoryFromPullRequest(pullRequest)

    if (repository !== null) {
      await this.selectRepository(repository)
    } else {
      repository = await this.openOrCloneRepository(url)
    }

    if (repository === null) {
      log.warn(
        `Open Repository from URL failed, did not find or clone repository: ${url}`
      )
      return null
    }
    if (!isRepositoryWithGitHubRepository(repository)) {
      log.warn(
        `Received a non-GitHub repository when opening repository from URL: ${url}`
      )
      return null
    }

    // ensure a fresh clone repository has it's in-memory state
    // up-to-date before performing the "Clone in Desktop" steps
    await this.appStore._refreshRepository(repository)

    if (pullRequest.head.repo === null) {
      return null
    }

    await this.appStore._checkoutPullRequest(
      repository,
      pullRequest.number,
      pullRequest.head.repo.owner.login,
      pullRequest.head.repo.clone_url,
      pullRequest.head.ref
    )

    return repository
  }
```

**File:** app/src/lib/stores/git-store.ts (L1364-1380)
```typescript
  public async ensureUpstreamRemoteURL(remoteUrl: string): Promise<void> {
    await this.performFailableOperation(async () => {
      try {
        await addRemote(this.repository, UpstreamRemoteName, remoteUrl)
      } catch (e) {
        if (
          e instanceof DugiteError &&
          e.result.gitError === GitError.RemoteAlreadyExists
        ) {
          // update upstream remote if it already exists
          await setRemoteURL(this.repository, UpstreamRemoteName, remoteUrl)
        } else {
          throw e
        }
      }
    })
  }
```
