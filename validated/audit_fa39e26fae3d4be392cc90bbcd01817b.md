### Title
Deep link "Open in Desktop" pull-request action silently checks out an attacker-controlled fork branch into an already-trusted local repository - ([File: app/src/ui/dispatcher/dispatcher.ts])

### Summary
`x-github-client://openRepo/<url>?pr=<n>` deep links (registered as OS protocol handlers, so clickable from any webpage or email) are parsed by `parseAppURL` into an `IOpenRepositoryFromURLAction` and routed to `Dispatcher.openRepositoryFromUrl` / `openPullRequestFromUrl`. [1](#0-0) [2](#0-1)  When a `pr` parameter is present, Desktop fetches the PR from the GitHub API using attacker-supplied `url`/`pr` values, matches it against the user's *already opened, already trusted* local repositories purely by owner/name string comparison, and then automatically adds the PR's `head.repo.clone_url` (fully attacker-controlled - any fork) as a remote, fetches it, and runs `git checkout` on the fork's branch into that trusted repository - with no confirmation dialog. [3](#0-2) 

### Finding Description
The broken invariant mirrors the external report's core flaw: an operation that has real side effects (here, mutating a local repository's working tree) is executed by trusting caller-controlled "source"/"target" identity fields without validating that they legitimately belong together, and without the normal safety checks that apply elsewhere in the same subsystem.

1. `openPullRequestFromUrl(url, pr)` takes the `url` and `pr` values straight from the deep-link query string (both attacker-controlled) and calls `this.appStore.fetchPullRequest(url, pr)` to retrieve a real `IAPIPullRequest` object from GitHub. [4](#0-3) 
2. `getRepositoryFromPullRequest` then looks for a **repository the user already has open in Desktop** whose GitHub origin/upstream URL matches the PR's `head.repo.clone_url` (the fork) *or* `base.repo.clone_url`, using only a loose owner/name/hostname comparison (`urlsMatch`) - not any cryptographic or session-bound identity check. [5](#0-4) 
3. If a match is found, Desktop calls `selectRepository(repository)` and then unconditionally calls `_checkoutPullRequest(repository, pullRequest.number, pullRequest.head.repo.owner.login, pullRequest.head.repo.clone_url, pullRequest.head.ref)`. [6](#0-5) 
4. `_checkoutPullRequest` → `_findPullRequestBranch` adds the fork URL as a new remote (or reuses an existing one) via `addRemote`, fetches it, and finally calls `_checkoutBranch`, which invokes `git checkout` and updates submodules — mutating the working directory of the repository that was matched. [7](#0-6) 

Unlike the `open-repository`/`filepath` case in the same function, which explicitly guards against absolute paths and directory traversal (`isAbsolute`, `resolveWithin`), [8](#0-7)  there is **no equivalent gate** before the PR-checkout path: no dialog asks the user to confirm "this link wants to check out a branch from *fork X* into *your existing repository Y*." The user only sees the effect of a completed `git checkout` after the fact — none of the existing "confirm checkout" dialogs found elsewhere in the app (e.g. `ConfirmCheckoutCommitDialog`) are wired into this deep-link flow. [9](#0-8) 

### Impact Explanation
An attacker who gets a victim to click a single OS-level link causes GitHub Desktop to:
- silently add an attacker-chosen git remote to a repository the victim already trusts and has open,
- `git fetch` that remote,
- `git checkout` a branch supplied by the attacker (any public fork, even one the attacker just pushed to), overwriting the victim's working directory contents in that trusted repository, and re-run submodule updates.

If the victim subsequently builds/runs the project (a normal workflow immediately following a checkout) or commits/pushes without close inspection, this is "silent corruption of what the user commits or pushes" and can lead to local code execution of attacker-supplied code — matching the explicit valid-impact categories in scope (attacker controls a GitHub API object / a deep link the user clicks, resulting in silent corruption of repository contents). No local access, malware, or leaked credentials are required — only clicking a URL.

### Likelihood Explanation
`x-github-client://` (and the legacy `github-mac`/`github-windows` schemes) are registered as OS-level default protocol handlers, [10](#0-9)  meaning any web page, email client, chat app, or malicious ad can trigger this flow with a single click, and it works even on repositories the user has never interacted with via this deep link before (any repo already cloned with matching owner/name). The only precondition is that the victim already has a local clone whose origin/upstream matches the targeted `owner/name` — a very common situation for popular OSS projects. This is a one-click, no-privilege, no-malware attack path.

### Recommendation
Apply the same "properly validate before acting" principle used elsewhere in the same function (e.g. the `filepath`/`resolveWithin` guard) to the pull-request checkout path:
1. Before calling `_checkoutPullRequest`, present a confirmation dialog naming the exact repository that will be modified, the fork URL, and the branch to be checked out, and require explicit user consent (mirroring `ConfirmCheckoutCommitDialog`).
2. Do not silently reuse an already-open, pre-existing local repository for a URL/PR combination it did not itself originate; require an explicit user action to opt a given local repo into being the target of "Open in Desktop" PR checkouts.
3. Tighten `doesRepositoryMatchUrl`/`urlsMatch` matching or otherwise bind it to the specific PR/host, so a crafted deep link cannot cause an unrelated existing repository to be treated as the checkout target.

### Proof of Concept
1. Victim has previously cloned `https://github.com/some-org/some-project` in GitHub Desktop (origin remote matches).
2. Attacker opens a PR against `some-org/some-project` from their own fork (`attacker/some-project`) containing a malicious build script/commit, or simply references any existing open PR number.
3. Attacker sends the victim a link:
   `x-github-client://openRepo/https://github.com/some-org/some-project?pr=<attacker_pr_number>`
4. Victim clicks the link. `main.ts`'s `open-url`/protocol handler invokes `handleAppURL` → `parseAppURL` → `window.sendURLAction(action)`. [2](#0-1) 
5. `Dispatcher.openRepositoryFromUrl` → `openPullRequestFromUrl` fetches the PR, matches the victim's already-open `some-org/some-project` repository, and calls `_checkoutPullRequest` with the attacker fork's `clone_url` and branch. [3](#0-2) 
6. Desktop adds the attacker's fork as a remote, fetches it, and runs `git checkout` on the attacker's branch inside the victim's trusted repository — all without any confirmation prompt. [7](#0-6)

### Citations

**File:** app/src/lib/parse-app-url.ts (L10-24)
```typescript
export interface IOpenRepositoryFromURLAction {
  readonly name: 'open-repository-from-url'

  /** the remote repository location associated with the "Open in Desktop" action */
  readonly url: string

  /** the optional branch name which should be checked out. use the default branch otherwise. */
  readonly branch: string | null

  /** the pull request number, if pull request originates from a fork of the repository */
  readonly pr: string | null

  /** the file to open after cloning the repository */
  readonly filepath: string | null
}
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1890-1938)
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1972)
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

**File:** app/src/lib/stores/app-store.ts (L8613-8721)
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

    return existingBranch
  }
```

**File:** app/src/ui/checkout/confirm-checkout-commit.tsx (L1-25)
```typescript
import * as React from 'react'
import { Dialog, DialogContent, DialogFooter } from '../dialog'
import { Repository } from '../../models/repository'
import { Dispatcher } from '../dispatcher'
import { Row } from '../lib/row'
import { OkCancelButtonGroup } from '../dialog/ok-cancel-button-group'
import { Checkbox, CheckboxValue } from '../lib/checkbox'
import { CommitOneLine } from '../../models/commit'

interface IConfirmCheckoutCommitProps {
  readonly dispatcher: Dispatcher
  readonly repository: Repository
  readonly commit: CommitOneLine
  readonly askForConfirmationOnCheckoutCommit: boolean
  readonly onDismissed: () => void
}

interface IConfirmCheckoutCommitState {
  readonly isCheckingOut: boolean
  readonly confirmCheckoutCommit: boolean
}
/**
 * Dialog to confirm checking out a commit
 */
export class ConfirmCheckoutCommitDialog extends React.Component<
```
