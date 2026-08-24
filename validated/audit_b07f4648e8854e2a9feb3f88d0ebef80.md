Based on the investigation, the `x-github-client://openrepo/` deep link handler exhibits the same broken-invariant pattern as the ERC4337 report: a deterministic, security-relevant action is executed automatically from attacker-controlled input, with no explicit user confirmation of the actual target/value being acted upon.

### Title
Deep-link "open-repository-from-url" silently checks out an attacker-chosen branch/PR without confirmation when a matching repository already exists locally - (File: `app/src/ui/dispatcher/dispatcher.ts`)

### Summary
`parseAppURL` accepts a `x-github-client://openrepo/<url>?branch=<b>&pr=<pr>` deep link and returns an `IOpenRepositoryFromURLAction`. [1](#0-0)  When dispatched, `openRepositoryFromUrl` looks at the `url`/`branch`/`pr` fields and, if a repository whose remote already matches that `url` exists in the user's repository list, it fetches and force-checks-out the attacker-supplied branch (or the head ref of an attacker-supplied PR) with **no popup, confirmation dialog, or "Clone repository" step** — unlike the case where the repository doesn't exist yet, which goes through the `CloneRepository` UI. [2](#0-1) 

### Finding Description
`openOrCloneRepository` first checks whether an existing repository's origin/upstream URL matches the deep-link URL via `doesRepositoryMatchUrl`/`urlsMatch`. [3](#0-2) [4](#0-3)  If a match is found, `selectRepository` is returned directly — bypassing the `CloneRepository` popup entirely. `openBranchNameFromUrl` then performs `_refreshRepository`, `_fetch`, and `checkoutLocalBranch` automatically: [5](#0-4)  `checkoutLocalBranch` will silently switch the working tree to the attacker-named branch as long as a local branch with that `nameWithoutRemote` exists (e.g. a previously fetched/known remote-tracking branch), with no user prompt of any kind. [6](#0-5)  For the `pr` variant, `openPullRequestFromUrl` similarly fetches and calls `_checkoutPullRequest` using the PR head repo/ref taken directly from the API response for the attacker-supplied PR number, again without a confirmation dialog. [7](#0-6) 

`checkoutBranch` runs `git checkout` in the repository, which triggers Git's `post-checkout` hook if one is present in the repository (installed by an earlier commit or by tooling), and updates submodules. [8](#0-7)  The hook execution machinery already recognizes `post-checkout` as a real hook that Desktop runs. [9](#0-8) 

Existing guards only cover the `branch` string format (`testForInvalidChars`) and `pr` numeric format at parse time [10](#0-9) , and only cover the post-checkout `filepath` traversal (`isAbsolute`/`resolveWithin`) [11](#0-10) . None of these guards validate *whether the user actually intended* to switch branches/checkout a PR on an already-open repository — the analogous "owner must match salt" check from the ERC4337 report has no equivalent here: the deep link's `url`/`branch`/`pr` fields are trusted to silently mutate repository state for repos that already exist, exactly as the factory trusted a `salt` value without validating the embedded owner.

### Impact Explanation
A link an attacker gets a victim to click (email, chat, malicious webpage using the registered `x-github-client://` protocol handler) can silently force an already-open GitHub Desktop repository to check out a branch or PR of the attacker's choosing, with no dialog shown to the user. If the repository has a `post-checkout` hook, a `.vscode/tasks.json` auto-run configuration, or build tooling that reacts to file changes, this can lead to local code execution. At minimum it silently corrupts the state of what the user is working on/about to commit — the user may believe they are still on their own branch while running builds, tests, or making commits against attacker-controlled content, satisfying "silent corruption of what the user commits or pushes."

### Likelihood Explanation
The protocol handler is registered by default on all platforms (`x-github-client`, plus legacy `github-mac`/`github-windows`) and is invoked automatically by the OS when the user clicks a matching link — no local access, admin rights, or prior compromise required. [12](#0-11)  The only precondition is that the victim already has the target repository added in Desktop (a very common case for active users), and that the requested branch exists in their local branch list (trivially satisfiable if the victim has ever fetched from a shared/public repo, or the attacker targets a repo where all branches are visible).

### Recommendation
Route the "existing repository already matches URL" branch/PR checkout path through the same explicit user-confirmation surface used for the clone path (e.g., a confirmation popup showing "Desktop wants to switch to branch/PR X in repository Y — Continue?") before calling `_fetch`/`checkoutLocalBranch`/`_checkoutPullRequest`. At minimum, require an explicit user gesture inside the app (not just an OS-level link click) before mutating the working tree of an already-opened repository from a deep link, mirroring the recommendation in the ERC4337 report to "strictly enforce" that the security-relevant identifier (the target branch/PR) is confirmed against the actor's intent rather than trusted implicitly.

### Proof of Concept
1. Victim has GitHub Desktop installed with `https://github.com/victim-org/webapp` already added as a repository, and has previously fetched a branch/PR from a shared contributor (so a matching local branch name exists, or use the `pr` parameter which doesn't require this).
2. Attacker hosts (or emails) a link: `x-github-client://openRepo/https://github.com/victim-org/webapp?pr=1337` where PR #1337 is an attacker-controlled fork/branch containing a malicious `post-checkout` hook or build script.
3. Victim clicks the link. The OS invokes GitHub Desktop's registered protocol handler, `handleAppURL` parses it via `parseAppURL`, and `dispatchURLAction` routes to `openRepositoryFromUrl` → `openPullRequestFromUrl`. [13](#0-12) [7](#0-6) 
4. Because the repository already exists in Desktop, no `CloneRepository` dialog or confirmation is ever shown; the app silently fetches and checks out the attacker's PR head into the victim's local working copy, potentially running the malicious hook.

### Citations

**File:** app/src/lib/parse-app-url.ts (L98-125)
```typescript
  if (actionName === 'openrepo') {
    const pr = getQueryStringValue(query, 'pr')
    const branch = getQueryStringValue(query, 'branch')
    const filepath = getQueryStringValue(query, 'filepath')

    if (pr != null) {
      if (!/^\d+$/.test(pr)) {
        return unknown
      }

      // we also expect the branch for a forked PR to be a given ref format
      if (branch != null && !/^pr\/\d+$/.test(branch)) {
        return unknown
      }
    }

    if (branch != null && testForInvalidChars(branch)) {
      return unknown
    }

    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
    }
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1996)
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

    if (repository === null) {
      return
    }

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
    }
  }

  private async openBranchNameFromUrl(
    url: string,
    branchName: string
  ): Promise<Repository | null> {
    const repository = await this.openOrCloneRepository(url)

    if (repository === null) {
      return null
    }

    // ensure a fresh clone repository has it's in-memory state
    // up-to-date before performing the "Clone in Desktop" steps
    await this.appStore._refreshRepository(repository)

    // if the repo has a remote, fetch before switching branches to ensure
    // the checkout will be successful. This operation could be a no-op.
    await this.appStore._fetch(repository, FetchType.UserInitiatedTask)

    await this.checkoutLocalBranch(repository, branchName)

    return repository
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2188-2212)
```typescript
  private async checkoutLocalBranch(repository: Repository, branch: string) {
    let shouldCheckoutBranch = true

    const state = this.repositoryStateManager.get(repository)
    const branches = state.branchesState.allBranches

    const { tip } = state.branchesState

    if (tip.kind === TipState.Valid) {
      shouldCheckoutBranch = tip.branch.nameWithoutRemote !== branch
    }

    const localBranch = branches.find(b => b.nameWithoutRemote === branch)

    // N.B: This looks weird, and it is. _checkoutBranch used
    // to behave this way (silently ignoring checkout) when given
    // a branch name string that does not correspond to a local branch
    // in the git store. When rewriting _checkoutBranch
    // to remove the support for string branch names the behavior
    // was moved up to this method to not alter the current behavior.
    //
    // https://youtu.be/IjmtVKOAHPM
    if (shouldCheckoutBranch && localBranch !== undefined) {
      await this.checkoutBranch(repository, localBranch)
    }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2215-2233)
```typescript
  private async openOrCloneRepository(url: string): Promise<Repository | null> {
    const state = this.appStore.getState()
    const repositories = state.repositories
    const existingRepository = repositories.find(r =>
      this.doesRepositoryMatchUrl(r, url)
    )

    if (existingRepository) {
      return await this.selectRepository(existingRepository)
    }

    return this.appStore._startOpenInDesktop(() => {
      this.changeCloneRepositoriesTab(CloneRepositoryTab.Generic)
      this.showPopup({
        type: PopupType.CloneRepository,
        initialURL: url,
      })
    })
  }
```

**File:** app/src/lib/git/checkout.ts (L102-146)
```typescript
export async function checkoutBranch(
  repository: Repository,
  branch: Branch,
  currentRemote: IRemote | null,
  progressCallback?: ProgressCallback,
  allowFileProtocol: boolean = false
): Promise<true> {
  const title = `Checking out branch ${branch.name}`
  const opts = await getCheckoutOpts(
    repository,
    title,
    branch.name,
    currentRemote,
    progressCallback
      ? clampProgress(0, CheckoutStepWeight, progressCallback)
      : undefined,
    `Switching to ${__DARWIN__ ? 'Branch' : 'branch'}`
  )

  const baseArgs = getCheckoutArgs(progressCallback)
  const args = [...baseArgs, ...(await getBranchCheckoutArgs(branch))]

  await git(args, repository.path, 'checkoutBranch', opts)

  // Update submodules after checkout
  await updateSubmodulesAfterOperation(
    repository,
    currentRemote,
    progressCallback
      ? clampProgress<ICheckoutProgress>(
          CheckoutStepWeight,
          1,
          progressCallback
        )
      : undefined,
    'checkout',
    title,
    branch.name,
    allowFileProtocol
  )

  // we return `true` here so `GitStore.performFailableGitOperation`
  // will return _something_ differentiable from `undefined` if this succeeds
  return true
}
```

**File:** app/src/lib/hooks/get-repo-hooks.ts (L10-28)
```typescript
const knownHooks = [
  'applypatch-msg',
  'pre-applypatch',
  'post-applypatch',
  'pre-commit',
  'pre-merge-commit',
  'prepare-commit-msg',
  'commit-msg',
  'post-commit',
  'pre-rebase',
  'post-checkout',
  'post-merge',
  'pre-push',
  'pre-receive',
  'update',
  'proc-receive',
  'post-receive',
  'post-update',
  'reference-transaction',
```

**File:** app/src/main-process/main.ts (L102-116)
```typescript
/** Extra argument for the protocol launcher on Windows */
const protocolLauncherArg = '--protocol-launcher'

const possibleProtocols = new Set(['x-github-client'])
if (__DEV_SECRETS__) {
  possibleProtocols.add('x-github-desktop-dev-auth')
} else {
  possibleProtocols.add('x-github-desktop-auth')
}
// Also support Desktop Classic's protocols.
if (__DARWIN__) {
  possibleProtocols.add('github-mac')
} else if (__WIN32__) {
  possibleProtocols.add('github-windows')
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
