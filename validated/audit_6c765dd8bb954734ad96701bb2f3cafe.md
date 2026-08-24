### Title
Deep link (`x-github-client://openRepo/...?pr=`) silently fetches and checks out an attacker‑chosen fork/branch into an existing local repository with no user confirmation - (File: `app/src/ui/dispatcher/dispatcher.ts`)

### Summary
GitHub Desktop registers a custom URL protocol handler that any webpage or message can invoke without prior authorization, and the handler for `openRepo` action with a `pr` parameter automatically resolves the pull request via the GitHub API, adds a remote pointing to whatever `clone_url` that PR head reports, fetches it, and checks it out onto the user's already‑existing local clone — all without any confirmation dialog or verification that the "caller" (the page/link) has any right to trigger this on the user's machine.

### Finding Description
The broken invariant mirrors the smart‑contract bug: an operation that mutates state tied to an object (there: `tokenId` owned by a user; here: a local `Repository` the user already has on disk) is triggered using attacker‑supplied identifiers, with no check that the triggering party is authorized to invoke it on behalf of the object's owner.

The flow:
1. `app.on('open-url', ...)` / the protocol‑launcher CLI path calls `handleAppURL` → `parseAppURL` for any `x-github-client://` URL, with no origin check on who invoked it [1](#0-0) .
2. `parseAppURL` extracts `openRepo` action with attacker‑controlled `url`, `pr`, `branch`, `filepath` fields [2](#0-1) .
3. `dispatchURLAction` routes `open-repository-from-url` to `openRepositoryFromUrl`, which — if a `pr` is present — calls `openPullRequestFromUrl(url, pr)` [3](#0-2) .
4. `openPullRequestFromUrl` fetches the PR from the GitHub API using the attacker‑supplied `pr` number, matches it to the user's **already existing** local repository by comparing `url` to the repo's GitHub `htmlURL` (`doesRepositoryMatchUrl` / `getRepositoryFromPullRequest`), selects that repository, and then calls `appStore._checkoutPullRequest` using `pullRequest.head.repo.clone_url` and `pullRequest.head.ref` — values that come entirely from the PR's head, which is fully controlled by whoever opened that PR (the attacker) [4](#0-3) .
5. `_checkoutPullRequest` → `_findPullRequestBranch` silently adds a new remote (`forkPullRequestRemoteName`) pointing at the attacker's fork if none exists, fetches it, creates/checks out a `pr/<number>` branch, and returns it for immediate checkout — with no user prompt at any step [5](#0-4) .

None of the existing guards address this: `doesRepositoryMatchUrl` only checks that the *base* repo URL matches (that's public knowledge, e.g. any popular OSS project), not that the *PR* or its head fork is trustworthy [6](#0-5) . `filepath` handling defends against path traversal (`isAbsolute` / `resolveWithin` checks) [7](#0-6) , but there is no equivalent validation or confirmation gate for the `pr` parameter before the fetch+checkout side effects occur. The `isTrustedIPCSender` mechanism only protects IPC from the renderer, not the OS‑level protocol handler entry point that ultimately drives this same code path [8](#0-7) .

### Impact Explanation
This lets an unprivileged attacker (anyone who can open a pull request against any public GitHub repository, and who can get a victim to click one link) cause Desktop to:
- Add an attacker‑named remote and fetch arbitrary attacker‑controlled repository content into the victim's existing local clone.
- Silently check out that content as a new/updated branch (`pr/<number>`) in the user's working directory — without any "Do you want to open/checkout this?" confirmation.

If the checked‑out branch contains malicious build scripts, git hooks, CI config, or editor/IDE config that the victim's tooling auto‑executes when the branch is checked out (a very common CI/IDE auto‑run behavior), this becomes a code‑execution primitive achieved purely by tricking the user into clicking a crafted `x-github-client://` link — matching the "attacker controls a fetched repository" + "link the user clicks" + "silent corruption of what the user is working on" categories called out as valid impact classes.

### Likelihood Explanation
Medium. Requires: (1) attacker opens a PR against some public repo the target already has cloned in Desktop (trivial, unprivileged), and (2) the victim clicks a link using the registered `x-github-client://openRepo/...&pr=N` scheme (deliverable via email, chat, or any webpage, since protocol handlers are globally registered by the OS, not scoped to github.com). No local access, admin rights, or pre‑existing malware is required. The main friction is that the victim must already have the target repository cloned locally with Desktop as the default handler for the URL scheme.

### Recommendation
Before performing any fetch/checkout driven by a URL‑ or CLI‑triggered `pr` action, surface an explicit confirmation dialog to the user showing the actual PR author/fork/branch that will be fetched and checked out, rather than performing the remote‑add/fetch/checkout silently. Additionally, consider validating that the invoking `url`'s host is `github.com`/a known GitHub Enterprise host before trusting `pr` parameters at all.

### Proof of Concept
1. Victim has a public repository (e.g. `https://github.com/some/popular-repo`) cloned in GitHub Desktop.
2. Attacker forks `some/popular-repo`, pushes a branch, and opens PR #N against it (no special permission required).
3. Attacker sends the victim a link: `x-github-client://openRepo/https://github.com/some/popular-repo?pr=N`.
4. Victim clicks the link (e.g., embedded in a webpage or chat message). Desktop's OS protocol handler invokes `handleAppURL` → `parseAppURL` → `dispatchURLAction` → `openPullRequestFromUrl` → `appStore._checkoutPullRequest`, which adds a `github-desktop-<attacker>` remote, fetches it, and checks out branch `pr/N` in the victim's existing local clone of `some/popular-repo` without any confirmation prompt [5](#0-4) .

Note: I could not fully verify within the tool budget whether any confirmation dialog exists further up the call chain in the UI layer (e.g., a popup triggered before `_checkoutBranch` is invoked from this specific code path) — the search only confirmed the store/dispatcher logic performs the fetch and checkout directly with no dialog shown in the traced code. A Devin session with full repo access would be needed to definitively confirm the absence of any intervening confirmation UI.

### Citations

**File:** app/src/main-process/main.ts (L204-210)
```typescript
app.on('will-finish-launching', () => {
  // macOS only
  app.on('open-url', (event, url) => {
    event.preventDefault()
    handleAppURL(url)
  })
})
```

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

**File:** app/src/main-process/trusted-ipc-sender.ts (L1-18)
```typescript
import { WebContents } from 'electron'

// WebContents id of trusted senders of IPC messages. This is used to verify
// that only IPC messages sent from trusted senders are handled, as recommended
// by the Electron security documentation:
// https://github.com/electron/electron/blob/main/docs/tutorial/security.md#17-validate-the-sender-of-all-ipc-messages
const trustedSenders = new Set<number>()

/** Adds a WebContents instance to the set of trusted IPC senders. */
export const addTrustedIPCSender = (wc: WebContents) => {
  trustedSenders.add(wc.id)
  wc.on('destroyed', () => trustedSenders.delete(wc.id))
}

/** Returns true if the given WebContents is a trusted sender of IPC messages. */
export const isTrustedIPCSender = (wc: WebContents) => trustedSenders.has(wc.id)


```
