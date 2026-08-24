### Title
Deep-link "Open PR in Desktop" silently checks out an attacker-controlled fork branch into any locally-matching repository - ([File: app/src/ui/dispatcher/dispatcher.ts])

### Summary
The `x-github-client://openrepo/...?pr=<n>` deep-link handler resolves which local repository to operate on by matching the pull request's **base** repository URL against any already-open repository, then automatically adds a remote for the PR's **head** (fork) repository and checks out that branch — all without confirming that the link actually refers to a repository/PR the user intended to open, mirroring the "broad, insufficiently scoped access check" pattern from the source report (a permissive match stands in for a real authorization/consent check).

### Finding Description
Clicking a `desktop://openrepo/<owner>/<repo>?pr=<n>` link (an attacker-controlled, user-clicked artifact) causes the app to:

1. Parse the URL via `parseAppURL` into an `IOpenRepositoryFromURLAction` and dispatch it: [1](#0-0) 
2. Call `openRepositoryFromUrl`, which for a `pr` action calls `openPullRequestFromUrl(url, pr)`: [2](#0-1) 
3. `openPullRequestFromUrl` fetches PR data from the API for the number/owner/repo in the URL, then tries to find a **local repository match** via `getRepositoryFromPullRequest`, falling back to `openOrCloneRepository(url)`: [3](#0-2) 
4. `getRepositoryFromPullRequest` matches an existing open repository if its origin/upstream URL equals **either** the PR's head clone URL **or its base clone URL**: [4](#0-3) 
5. `doesRepositoryMatchUrl` performs this comparison against `originRepoUrl` or `upstreamRepoUrl` with no further scoping (e.g., no check that the *PR itself* was authored/expected by the user, nor that the head repo is trusted): [5](#0-4) 
6. Once a match is found, the app immediately (no confirmation dialog) calls `_checkoutPullRequest`, which via `_findPullRequestBranch` **creates a remote pointing at the PR head's clone URL if one doesn't exist**, fetches it, and checks out a new local branch `pr/<n>` tracking that remote: [6](#0-5) [7](#0-6) 

The broken invariant: matching "any open repository whose origin **or upstream** equals the PR's **base** repo" is far broader than "the repository the user meant to act on." Any public repository the victim has cloned (e.g., a popular OSS project) can be used as the base of a PR opened by *anyone*, including the attacker, from an attacker-controlled fork. The attacker doesn't need write access to the base repo, doesn't need to control the target repository, and doesn't need any account/permission — they only need to open a PR against a public repo and craft a link. Existing guards (`urlMatchesRemote`, `caseInsensitiveEquals` on hostname/owner/name in `repository-matching.ts`) only verify the URL is *syntactically* the same repository — they provide no notion of trust or consent, exactly like `isSafeLead` in the reference report checking a broad role instead of the specific permission needed for the specific action.

### Impact Explanation
A user who clicks a malicious deep link will have Desktop automatically:
- Add a new git remote pointing to an attacker-chosen fork URL in an existing, unrelated local repository.
- Fetch and check out attacker-controlled code into a newly created local branch of that repository.

This is a silent corruption of the user's local working state driven entirely by attacker-supplied content (the PR and its head repository), satisfying "silent corruption of what the user commits/pushes" and potential "code execution" if the victim later builds/runs that checked-out branch, opens it in an editor with auto-run tasks, etc. No signature/consent step gates the remote-add + checkout sequence beyond the initial link click, which is the class of unprompted, attacker-triggered action this task considers in-scope.

### Likelihood Explanation
Requires only that the victim (1) has at least one popular/public repository cloned in Desktop, and (2) clicks a crafted link (e.g., `x-github-client://openrepo/<owner>/<popular-repo>?pr=<n>` for a PR the attacker opened from their own fork). Both are realistic, low-friction conditions — no special privileges, no local access, and no social engineering beyond a single link click (in scope per the task's rules).

### Recommendation
- Do not silently match the PR to an already-open repository based on the **base** repo URL; only allow the match when the URL supplied in the deep link itself resolves to that exact GitHub repository (owner/name), and require an explicit user confirmation dialog before adding a remote and checking out a branch that originates from a third-party fork.
- Surface the head-repository owner/branch in a confirmation prompt prior to calling `_checkoutPullRequest`, similar to safeguards already used for e.g. absolute-path checks in `openRepositoryFromUrl`'s `filepath` handling: [8](#0-7) 
- Consider scoping `getRepositoryFromPullRequest` matches to only the repository named in the deep link URL itself, rather than any open repository whose base/upstream happens to match.

### Proof of Concept
1. Attacker forks a well-known public repository `victim-org/popular-repo` (which the victim already has cloned in GitHub Desktop) and opens a PR from their fork's branch `evil-branch` against `victim-org/popular-repo`.
2. Attacker crafts the link: `x-github-client://openrepo/victim-org/popular-repo?pr=<attacker-pr-number>`.
3. Victim, who has `victim-org/popular-repo` open in Desktop, clicks the link (e.g., embedded on a webpage or chat message).
4. `handleAppURL` → `parseAppURL` → `dispatchURLAction` → `openRepositoryFromUrl` → `openPullRequestFromUrl` executes: [9](#0-8) 
5. `getRepositoryFromPullRequest` matches the victim's already-open `victim-org/popular-repo` via the PR's base URL. [10](#0-9) 
6. `_checkoutPullRequest` adds a remote for the attacker's fork clone URL and checks out `pr/<n>` locally without any confirmation dialog. [6](#0-5) 

Note: I was unable to fully trace whether any UI-level confirmation dialog wraps `dispatchURLAction`/`checkoutPullRequest` calls from a global click (I only confirmed the direct programmatic call chain has none). If such a dialog exists elsewhere in the renderer bootstrap, it would need to be reviewed for whether it discloses the head repo/fork identity before the checkout — I could not locate one in the reachable code paths I reviewed.

### Citations

**File:** app/src/ui/dispatcher/dispatcher.ts (L1890-1918)
```typescript
  private getRepositoryFromPullRequest(
    pullRequest: IAPIPullRequest
  ): RepositoryWithGitHubRepository | null {
    const state = this.appStore.getState()
    const repositories = state.repositories
    const headUrl = pullRequest.head.repo?.clone_url
    const baseUrl = pullRequest.base.repo?.clone_url

    // This likely means that the base repository has been deleted
    // and we don't support checking out from refs/pulls/NNN/head
    // yet so we'll bail for now.
    if (headUrl === undefined || baseUrl === undefined) {
      return null
    }

    for (const repository of repositories) {
      if (this.doesRepositoryMatchUrl(repository, headUrl)) {
        return repository
      }
    }

    for (const repository of repositories) {
      if (this.doesRepositoryMatchUrl(repository, baseUrl)) {
        return repository
      }
    }

    return null
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1920-1938)
```typescript
  private doesRepositoryMatchUrl(
    repo: Repository | CloningRepository,
    url: string
  ): repo is RepositoryWithGitHubRepository {
    if (repo instanceof Repository && isRepositoryWithGitHubRepository(repo)) {
      const originRepoUrl = repo.gitHubRepository.htmlURL
      const upstreamRepoUrl = repo.gitHubRepository.parent?.htmlURL ?? null

      if (originRepoUrl !== null && urlsMatch(originRepoUrl, url)) {
        return true
      }

      if (upstreamRepoUrl !== null && urlsMatch(upstreamRepoUrl, url)) {
        return true
      }
    }

    return false
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1951)
```typescript
  private async openRepositoryFromUrl(action: IOpenRepositoryFromURLAction) {
    const { url, pr, branch, filepath } = action

    let repository: Repository | null

    if (pr !== null) {
      repository = await this.openPullRequestFromUrl(url, pr)
    } else if (branch !== null) {
      repository = await this.openBranchNameFromUrl(url, branch)
    } else {
      repository = await this.openOrCloneRepository(url)
    }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1971)
```typescript
    if (filepath !== null) {
      if (isAbsolute(filepath)) {
        log.error(`Refusing to open absolute path: ${filepath}`)
        return
      }

      const resolved = await resolveWithin(repository.path, filepath)

      if (resolved !== null) {
        shell.showItemInFolder(resolved)
      } else {
        log.error(
          `Prevented attempt to open path outside of the repository root: ${filepath}`
        )
      }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1998-2016)
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
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2118-2120)
```typescript
      case 'open-repository-from-url':
        this.openRepositoryFromUrl(action)
        break
```

**File:** app/src/lib/stores/app-store.ts (L8613-8631)
```typescript
  public async _checkoutPullRequest(
    repository: RepositoryWithGitHubRepository,
    prNumber: number,
    headRepoOwner: string,
    headCloneUrl: string,
    headRefName: string
  ): Promise<void> {
    const prBranch = await this._findPullRequestBranch(
      repository,
      prNumber,
      headRepoOwner,
      headCloneUrl,
      headRefName
    )
    if (prBranch !== undefined) {
      await this._checkoutBranch(repository, prBranch)
      this.statsStore.increment('prBranchCheckouts')
    }
  }
```

**File:** app/src/lib/stores/app-store.ts (L8633-8718)
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
```

**File:** app/src/main-process/main.ts (L159-168)
```typescript
function handleAppURL(url: string) {
  log.info('Processing protocol url')
  const action = parseAppURL(url)
  onDidLoad(window => {
    // This manual focus call _shouldn't_ be necessary, but is for Chrome on
    // macOS. See https://github.com/desktop/desktop/issues/973.
    window.focus()
    window.sendURLAction(action)
  })
}
```
