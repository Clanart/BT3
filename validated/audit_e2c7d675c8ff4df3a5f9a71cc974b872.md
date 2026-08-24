## Title
Deep link `x-github-client://openRepo/...?branch=<attacker-branch>` silently fetches and checks out an attacker-chosen branch with no user confirmation - (File: app/src/ui/dispatcher/dispatcher.ts)

### Summary
The external report describes a state-changing action (`Claim Rewards`) that a smart contract exposes to any caller without an authorization/consent check, letting the attacker trigger it unprompted. The closest reachable analog in GitHub Desktop is the `open-repository-from-url` deep-link action: for a repository the user already has open in Desktop, the `branch` parameter of an `x-github-client://openRepo/...` URL causes Desktop to automatically `fetch` and `git checkout` an attacker-specified ref with **no confirmation dialog**, unlike the sibling `filepath` parameter which is explicitly validated against path traversal.

### Finding Description
`parseAppURL` in [1](#0-0)  parses `openRepo` deep links into an `IOpenRepositoryFromURLAction` carrying an attacker-controlled `url` and `branch`. Validation only rejects branch names containing "invalid chars" via `testForInvalidChars`; it does not restrict which branch or verify user intent.

`Dispatcher.openRepositoryFromUrl` routes this to `openBranchNameFromUrl` when a `branch` is present: [2](#0-1) 

`openBranchNameFromUrl` resolves the existing repository (matched purely by comparing the GitHub HTML URL, `doesRepositoryMatchUrl`), immediately calls `this.appStore._fetch(...)`, and then `this.checkoutLocalBranch(repository, branchName)` — all without any popup, confirmation, or scoping to "known/trusted" branches: [3](#0-2) 

This contrasts with the `filepath` handling in the very same function, which explicitly guards against path traversal (`isAbsolute` check and `resolveWithin`) before touching the filesystem: [4](#0-3) 

No equivalent guard exists for the `branch` parameter: the broken invariant is "checking out a ref should require either it belong to a validated set (e.g., a PR-associated ref like the `pr` action's `^pr\/\d+$` regex) or explicit user confirmation" — but the plain `branch` action skips both checks that the PR path enforces (`parse-app-url.ts:103-112`).

### Impact Explanation
Because the checkout is silent, an attacker who can get a victim to click a crafted `x-github-client://openRepo/<victim-repo-url>?branch=<attacker-branch>` link (e.g., embedded in a webpage, chat message, or malicious PR/issue comment rendered outside GitHub Desktop's sandboxed markdown viewer) can force Desktop to fetch and switch the user's working tree in an already-cloned repository to any branch that exists on the remote — including branches the user never intended to check out. If the target repository has git hooks, build scripts, editor config, or CI files (e.g., `.vscode/tasks.json`, `Makefile`, npm `postinstall` scripts triggered by IDEs), silently landing an attacker-chosen branch onto disk sets up a path toward local code execution once the user's editor/IDE or subsequent Desktop actions (e.g., committing, pulling) process that content. At minimum, it silently corrupts the state of what the user is working on/about to commit — a working tree switch the user did not request.

### Likelihood Explanation
Likelihood is limited by the requirement that the user already have the target repository added in Desktop and click an attacker-supplied deep link (`x-github-client://` protocol registered by Desktop). No authentication, admin rights, or local access is needed — only a click, which is explicitly a valid analog trigger per this task's rules ("a link or deep link the user clicks"). The `branch` value is validated only for character set, not for provenance, so any existing branch name on the remote qualifies.

### Recommendation
Require explicit user confirmation (a dialog similar to `PopupType.CloneRepository`) before performing a fetch+checkout triggered by an `open-repository-from-url` action when the target repository is already present locally, rather than performing the branch switch unconditionally in `openBranchNameFromUrl`. At minimum, surface the branch name and remote URL to the user and require an explicit "Switch Branch" confirmation, mirroring the caution already applied to the `filepath` parameter in the same function.

### Proof of Concept
1. Add and select a repository in GitHub Desktop, e.g. `https://github.com/octokit/octokit.net`, that has an attacker-created branch `evil-branch` on the remote.
2. Attacker crafts and delivers (e.g., via a link in a webpage or chat) the URL:
   `x-github-client://openRepo/https://github.com/octokit/octokit.net?branch=evil-branch`
3. Victim clicks the link with Desktop running.
4. `parseAppURL` classifies it as `open-repository-from-url` with `branch: 'evil-branch'` [5](#0-4) .
5. `Dispatcher.openRepositoryFromUrl` -> `openBranchNameFromUrl` runs `_fetch` then `checkoutLocalBranch` with no dialog shown to the user [3](#0-2) .
6. The victim's working directory is now silently on `evil-branch` without any confirmation step, unlike the parallel `filepath`/`pr` validations in the same code path.

**Note on evidence limits:** I could not fully trace `checkoutLocalBranch`'s exact implementation (helper resolving `branchName` to a `Branch` object) within the indexed snippets, nor confirm whether Desktop runs any git hooks (e.g., `post-checkout`) by default during this flow — the codebase index did not surface hook-execution configuration for the checkout path beyond `app/src/lib/hooks/with-hooks-env.ts`, which was not fully inspected. If hooks are not executed or are sandboxed, the severity would be limited to unwanted working-tree corruption rather than code execution; a full review of `git/checkout.ts` and hook configuration is recommended to determine the ceiling of impact.

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
