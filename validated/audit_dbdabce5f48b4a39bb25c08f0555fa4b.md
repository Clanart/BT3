### Title
Deep-link "Open PR from Desktop" resolves attacker-controlled PR objects to an unrelated, already-trusted local repository by matching on mutable owner/name strings instead of a stable repository identity - ([File: app/src/ui/dispatcher/dispatcher.ts])

### Summary
The report's root cause is that the bridge used a non-unique, reusable identifier (`chain-id`) to distinguish the source and destination of a transfer, so when that identifier collided the contract could not tell the two sides apart. GitHub Desktop has the same class of bug in its "Open in Desktop" / "Open PR from Desktop" deep-link handling: repositories are matched to already-open local clones purely by parsing the *mutable* `owner/name` (and hostname) strings out of a clone URL, never by GitHub's stable numeric repository id. Because GitHub allows repository/owner names to be deleted, renamed, and reused, the same `owner/name` string can legitimately point to two completely different repositories over time — an exact analog of the same "chain-id" being reused for two different chains.

### Finding Description
The matching primitive is `urlMatchesRemote`/`urlsMatch` in `repository-matching.ts`, which compares only `hostname`, `owner`, and `name` parsed by `parseRemote`/`parseRepositoryIdentifier`: [1](#0-0) [2](#0-1) 

This is used by `Dispatcher.doesRepositoryMatchUrl`/`getRepositoryFromPullRequest` to pick which *already-open, already-trusted* local repository a PR deep link should apply to, by comparing the PR's `head`/`base` clone URLs against the `htmlURL` of the local repos: [3](#0-2) 

The URL comes from the OS-level protocol handler (`x-github-client://openRepo/<url>?pr=<n>`), which is fully attacker-controlled content the user only has to click: [4](#0-3) [5](#0-4) 

Once `openPullRequestFromUrl` resolves a matching existing repository, it does not re-verify that this repository is actually the same underlying GitHub repo (e.g., via stable repo id) — it proceeds straight to `_checkoutPullRequest`, which adds a fork remote and fetches/checks out the attacker-supplied branch into that pre-existing local working directory: [6](#0-5) [7](#0-6) 

The "chain-id" analog is the `owner/name` tuple: GitHub Desktop treats it as a stable identity for "this is the same repository," but GitHub itself does not guarantee this — deleted/renamed repos free up their `owner/name`, and it can be reclaimed by an unrelated party. Just as the bridge could not distinguish source vs. destination once the chain-id collided, Desktop cannot distinguish "the repository the user originally trusted and cloned" from "a different repository that now happens to share the same owner/name" once that string is reused. There is no stable-ID check (e.g., GitHub's numeric repository `id`) anywhere in this matching path, so the guard that would stop the collision does not exist.

### Impact Explanation
If an attacker can get control of a `owner/name` that a victim previously cloned in Desktop (e.g., a repository the victim forked from/collaborated on that was later deleted or renamed, allowing the attacker to claim the freed name, or a maliciously created PR whose `head`/`base` clone URL happens to string-match a repo the victim already has open), a single clicked deep link causes Desktop to silently reuse the victim's existing, trusted local clone, add an attacker-controlled remote, fetch, and check out an attacker-chosen branch/ref into that repository's working directory — without ever cloning a new, isolated location. This can silently corrupt what the user later commits/pushes from that directory, and depending on repo tooling (build scripts, git hooks, IDE auto-run configs) checked-out content can lead to code execution on the victim's machine.

### Likelihood Explanation
Requires only that the victim click a `x-github-client://` deep link (e.g., from a webpage, chat message, or malicious "Open in Desktop" button) — no local access, no credentials, no malware already on the host. The precondition (owner/name reuse or collision) is a known, achievable GitHub behavior (deleted/renamed repos free their name for reuse) rather than a contrived edge case, matching the "impact 5 / likelihood 5" rating pattern of the original report, though exploitation does require the attacker to first control content at the colliding `owner/name`.

### Recommendation
Match repositories to PR/deep-link targets using GitHub's stable numeric repository id (already available from the API as `GitHubRepository.dbID`/API `id` field) in addition to, or instead of, the string-based `owner/name`/hostname comparison in `urlMatchesRemote`, `urlsMatch`, and `doesRepositoryMatchUrl`. When no stable-id record exists for the currently-open repository (e.g., older DB rows), fail closed and require a fresh clone/explicit confirmation rather than silently reusing the local directory.

### Proof of Concept
1. Victim has previously cloned `github.com/legit-owner/project` in Desktop (or a repo whose owner/name is later freed by deletion/rename).
2. Attacker gains control of the `legit-owner/project` identifier (e.g., after it's deleted/renamed and becomes available, or by controlling a PR whose `head.repo`/`base.repo.clone_url` textually equals it) and pushes malicious content/branches there.
3. Attacker sends victim a link: `x-github-client://openRepo/https://github.com/legit-owner/project?pr=1`.
4. Victim clicks it; `main.ts`'s `open-url` handler → `parseAppURL` → `dispatchURLAction('open-repository-from-url')` → `openPullRequestFromUrl` fetches PR #1 from the (now attacker-controlled) repo.
5. `getRepositoryFromPullRequest`/`doesRepositoryMatchUrl` matches the PR's head/base clone URL string against the victim's existing local repo's `htmlURL` and selects it.
6. `_checkoutPullRequest`/`_findPullRequestBranch` adds a `github-desktop-<owner>` remote pointing at the attacker's repo and checks out the attacker's branch directly into the victim's pre-existing, trusted working directory.

### Citations

**File:** app/src/lib/repository-matching.ts (L90-118)
```typescript
export function urlMatchesRemote(url: string | null, remote: IRemote): boolean {
  if (url == null) {
    return false
  }

  const cloneUrl = parseRemote(url)
  const remoteUrl = parseRemote(remote.url)

  if (remoteUrl == null || cloneUrl == null) {
    return false
  }

  if (!caseInsensitiveEquals(remoteUrl.hostname, cloneUrl.hostname)) {
    return false
  }

  if (remoteUrl.owner == null || cloneUrl.owner == null) {
    return false
  }

  if (remoteUrl.name == null || cloneUrl.name == null) {
    return false
  }

  return (
    caseInsensitiveEquals(remoteUrl.owner, cloneUrl.owner) &&
    caseInsensitiveEquals(remoteUrl.name, cloneUrl.name)
  )
}
```

**File:** app/src/lib/repository-matching.ts (L137-148)
```typescript
export function urlsMatch(url1: string, url2: string) {
  const firstIdentifier = parseRepositoryIdentifier(url1)
  const secondIdentifier = parseRepositoryIdentifier(url2)

  return (
    firstIdentifier !== null &&
    secondIdentifier !== null &&
    firstIdentifier.hostname === secondIdentifier.hostname &&
    firstIdentifier.owner === secondIdentifier.owner &&
    firstIdentifier.name === secondIdentifier.name
  )
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

**File:** app/src/lib/parse-app-url.ts (L66-125)
```typescript
export function parseAppURL(url: string): URLActionType {
  const parsedURL = URL.parse(url, true)
  const hostname = parsedURL.hostname
  const unknown: IUnknownAction = { name: 'unknown', url }
  if (!hostname) {
    return unknown
  }

  const query = parsedURL.query

  const actionName = hostname.toLowerCase()
  if (actionName === 'oauth') {
    const code = getQueryStringValue(query, 'code')
    const state = getQueryStringValue(query, 'state')
    if (code != null && state != null) {
      return { name: 'oauth', code, state }
    } else {
      return unknown
    }
  }

  // we require something resembling a URL first
  // - bail out if it's not defined
  // - bail out if you only have `/`
  const pathName = parsedURL.pathname
  if (!pathName || pathName.length <= 1) {
    return unknown
  }

  // Trim the trailing / from the URL
  const parsedPath = pathName.substring(1)

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

**File:** app/src/lib/stores/app-store.ts (L8633-8721)
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

    return existingBranch
  }
```
