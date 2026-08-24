### Title
Unattended-consent deep-link auto-checkout: `x-github-client://openrepo` silently adds a stranger's remote and switches the working tree HEAD to attacker-controlled refs - (File: `app/src/ui/dispatcher/dispatcher.ts`)

### Summary
The PoolTogether report's broken invariant is: an authorization artifact created for a narrow purpose (approve a deposit) is consumed by a broader, unintended, higher-impact action (sponsor/delegate) because the contract cannot verify what the signer actually intended — it only verifies that the signature is *valid*, not that it matches the caller's true intent.

The Desktop analog is the `open-repository-from-url` deep-link action. `parseAppURL` in `app/src/lib/parse-app-url.ts` only validates that the incoming `x-github-client://openrepo/...` URL is *well-formed* (valid PR number regex, valid ref-name characters), not that the resulting operation matches what the user believes they are consenting to when clicking a link. A single click is broadened by `dispatcher.ts` into: matching an already-added local repository by URL, silently adding a brand-new git remote pointing at an attacker-supplied fork URL, fetching from it, and checking out the resulting ref — all without any confirmation dialog.

### Finding Description
`parseAppURL` (`app/src/lib/parse-app-url.ts:66-129`) extracts `pr`, `branch`, and `filepath` from any external caller of the `x-github-client`/`github-mac`/`github-windows` custom protocol (registered in `app/src/main-process/main.ts:105-116`, and dispatched via `app.on('open-url', ...)` at `app/src/main-process/main.ts:204-210`). This is attacker-reachable: any web page can trigger a custom-protocol navigation without requiring the user to have signed in, authenticated, or explicitly opted into anything beyond clicking a link. [1](#0-0) 

The parsed action is routed to `Dispatcher.openRepositoryFromUrl` (`app/src/ui/dispatcher/dispatcher.ts:1940-1973`), which for a `pr` parameter calls `openPullRequestFromUrl`: [2](#0-1) 

`getRepositoryFromPullRequest`/`doesRepositoryMatchUrl` match the deep link's `url` against an already-open repository's origin/upstream HTML URL — an attacker can predict this URL trivially since it is simply the public GitHub repository URL of any project a victim happens to have cloned in Desktop: [3](#0-2) 

Once matched, `appStore._checkoutPullRequest` → `_findPullRequestBranch` (`app/src/lib/stores/app-store.ts:8633-8721`) silently calls `addRemote(repository, forkRemoteName, headCloneUrl)` when no existing remote matches the PR's head clone URL, then fetches from it and checks out the resulting ref — again, no confirmation UI is shown to the user: [4](#0-3) 

For the non-PR `branch` case, `openBranchNameFromUrl` (`app/src/ui/dispatcher/dispatcher.ts:1975-1996`) fetches and checks out an arbitrary attacker-chosen branch name on the already-open repository with no confirmation: [5](#0-4) 

`checkoutLocalBranch` (`app/src/ui/dispatcher/dispatcher.ts:2188-2213`) performs the checkout directly if a matching local branch name exists, with no dialog, only bypassed by uncommitted-changes prompts that don't apply when the working tree is clean: [6](#0-5) 

The only guard applied anywhere in this path is syntactic (`testForInvalidChars`, PR-number regex, `isAbsolute`/`resolveWithin` for `filepath`). None of these guards check whether the *action being performed* (adding an untrusted remote, fetching arbitrary attacker infrastructure, silently switching HEAD) is something the user actually intended when they clicked a link — exactly the same broken invariant as the PoolTogether `sponsorWithPermit` issue: a validly-formed input (permit / URL) is treated as sufficient authorization for an action whose scope the user never confirmed.

### Impact Explanation
A user who clicks an attacker-supplied `x-github-client://openrepo/<known-repo-url>?pr=<n>` (or `?branch=<name>`) link — e.g. embedded on a compromised web page, in a chat message, or in a malicious "Open in Desktop" badge — will have Desktop, without any confirmation prompt:
- Add a new git remote pointing at attacker-controlled infrastructure (`addRemote`) to a repository the user already trusts.
- Fetch code from that untrusted remote.
- Silently switch the working tree HEAD (and possibly create a `pr/<n>` branch) to attacker-supplied content.

If the user is unaware the checkout changed (clean working directory, no confirmation shown), any subsequent local editing, committing, or pushing occurs against the wrong (attacker-influenced) branch — a silent corruption of what the user believes they are working on and may push. This does not by itself achieve remote code execution (git hooks are not distributed via `git fetch`/`checkout`), but it does satisfy the "silent corruption of what the user commits or pushes" impact category via an unprompted checkout initiated purely by a link click.

### Likelihood Explanation
Requires only that (a) the attacker knows/guesses a GitHub repository URL the victim already has cloned in Desktop (feasible for popular OSS projects or targeted spear-phishing where the target repo is known), and (b) the victim clicks a link. No credentials, local access, or additional user steps beyond a single click are required, and the custom protocol handler is registered globally by the installed app. This matches the report's "unnatural but plausible" reachability bar reasonably well, though the requirement to correctly guess/know an already-added repository URL and to reach a "clean working directory" state for the silent-checkout path reduces reliability somewhat compared to the original finding's broad exploitability.

### Recommendation
- Require explicit user confirmation before Desktop adds a new remote and fetches/checks out a ref triggered by an `open-repository-from-url` deep-link action, clearly surfacing the target clone URL/fork owner to the user before performing the fetch/checkout.
- Distinguish "open repository" (safe, matches existing state) from "add untrusted remote + fetch + checkout" (mutating, security-relevant) and gate the latter behind a confirmation dialog, similar to the existing uncommitted-changes confirmation flows already present in `_checkoutBranch`.
- Consider rate/state limiting or requiring the deep link to originate from a GitHub-known referrer context for PR/branch checkout actions specifically.

### Proof of Concept
1. Victim has `https://github.com/octo-org/target-repo` already added in GitHub Desktop with a clean working directory.
2. Attacker hosts a page (or sends a chat/email link) with:
   `<a href="x-github-client://openrepo/https://github.com/octo-org/target-repo?pr=999">Open in Desktop</a>`
   where PR #999 in `octo-org/target-repo` is an attacker-created pull request from `attacker/target-repo-fork`.
3. Victim clicks the link. Desktop's `open-url` handler (`app/src/main-process/main.ts:204-210`) → `parseAppURL` → `dispatchURLAction` → `openRepositoryFromUrl` → `openPullRequestFromUrl` (`app/src/ui/dispatcher/dispatcher.ts:1998-2048`) fires.
4. `_findPullRequestBranch` (`app/src/lib/stores/app-store.ts:8633-8721`) does not find an existing remote matching the fork's `clone_url`, so it silently calls `addRemote(repository, forkRemoteName, headCloneUrl)`, fetches, and checks out `pr/999` — with zero confirmation dialog shown to the victim.
5. The victim's working directory is now checked out to attacker-controlled code from a newly, silently added remote, without ever being told this happened.

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1975-1996)
```typescript
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2188-2213)
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
  }
```

**File:** app/src/lib/stores/app-store.ts (L8640-8662)
```typescript
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
```
