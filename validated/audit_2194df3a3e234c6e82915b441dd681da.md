Based on the investigation, the strongest analog is in how GitHub Desktop resolves an incoming `x-github-client://openRepo/...` deep link to a *local* repository using loose, non-unique matching criteria (owner/name/hostname), which is directly analogous to the "duplicate `scriptPubKeyHash`" collision problem — a non-unique identifier is trusted to route a privileged action (checking out a PR branch, fetching, or opening a file) to the wrong target.

### Title
Deep-link repository resolution uses non-unique owner/name matching, allowing attacker-controlled links to redirect Git operations to an unintended local repository - (File: app/src/ui/dispatcher/dispatcher.ts)

### Summary
When GitHub Desktop handles an `x-github-client://openRepo/...` URL (opened via a link the user clicks, e.g. from a GitHub PR page, email, or any attacker-controlled webpage), it resolves the target local repository using `urlsMatch`/`urlMatchesRemote`, which only compares `hostname`, `owner`, and `name` (case-insensitively) rather than a strong unique identifier such as the GitHub repository ID.

### Finding Description
The URL action `open-repository-from-url` is parsed by `parseAppURL` in [1](#0-0)  and dispatched to `openRepositoryFromUrl` in the `Dispatcher`. Repository identity is decided by `doesRepositoryMatchUrl`, which calls `urlsMatch` against the repository's `htmlURL`/parent `htmlURL`: [2](#0-1) 

`urlsMatch` in turn only compares the parsed `hostname`, `owner`, and `name` strings for equality: [3](#0-2) , and `urlMatchesRemote`/`repositoryMatchesRemote` use the same weak, string-based comparison rather than any server-side unique ID [4](#0-3) .

This is structurally the same broken invariant as the report: an identifier meant to uniquely designate a target (`scriptPubKeyHash` for onramps; `owner/name` for repositories) is not enforced to be unique/authoritative, so two different underlying entities can share the same "address." In Desktop's case:
- A repository can be renamed, transferred, or deleted and its `owner/name` reused by a different GitHub repository (GitHub allows name reuse after rename/delete).
- If a user has a stale local clone whose GitHub remote/`htmlURL` still reflects the old `owner/name`, and an attacker crafts (or simply waits for) a link `x-github-client://openRepo/https://github.com/<owner>/<name>?pr=...&branch=...&filepath=...`, Desktop will match this URL to the user's existing unrelated local repository via `matchExistingRepository`/`doesRepositoryMatchUrl` purely by string equality, not by verifying the repository's underlying GitHub ID still corresponds.
- Once matched, Desktop performs privileged operations against that local repo: `_checkoutPullRequest` (fetches attacker's fork/branch and checks it out) in `openPullRequestFromUrl` [5](#0-4) , or `checkoutLocalBranch` in `openBranchNameFromUrl` [6](#0-5) , or file reveal via `filepath` resolved with `resolveWithin` [7](#0-6) .

The existing guard (`resolveWithin`) only prevents path traversal outside the repo root; it does nothing to stop the wrong repository from being selected in the first place. There is no check that the `htmlURL`/remote's GitHub repository ID matches the ID returned by the GitHub API for the URL being opened.

### Impact Explanation
An attacker who controls a link the user clicks (comment, issue, malicious webpage, or a renamed/reused GitHub repo at a previously-known owner/name) can cause Desktop to add a remote, fetch an attacker-controlled branch/fork, and check it out into the user's pre-existing local repository — potentially one unrelated to the attacker's actual project. Because the branch name (e.g., `pr/123`) is deterministically generated and checked out automatically, the user's working directory can be silently mutated with attacker-supplied content in a repository the user did not intend to touch, and subsequent `git push` from the user could push that content upstream. This matches the "valid impact" criteria of a remote/link-triggered action causing silent corruption of what a user commits/pushes.

### Likelihood Explanation
Likelihood is Low-to-Medium: it requires the victim to have a local repository whose `owner/name` matches (via rename/transfer/deletion+recreation) a repository referenced by an attacker-controlled deep link, and for the user to click that link. This is a plausible but not trivially triggerable scenario, consistent with the "Low likelihood" rating given in the original report for the analogous bug class.

### Recommendation
When resolving a deep-link (`open-repository-from-url`) or PR-based repository match, verify identity using the GitHub repository's immutable numeric ID (already available via the API and stored as part of `GitHubRepository`) rather than relying solely on `owner/name/hostname` string comparisons in `urlsMatch`/`urlMatchesRemote`/`doesRepositoryMatchUrl`. If the ID cannot be verified (e.g., API call fails), prompt the user for confirmation before performing fetch/checkout operations against an existing local repository.

### Proof of Concept
1. User clones `github.com/victim-org/project-a` in Desktop; later `project-a` is renamed to `project-b`, and `project-a` name becomes available.
2. Attacker creates a new GitHub repo `github.com/victim-org/project-a` (same owner/name, different underlying repo ID) with a malicious branch/PR.
3. Attacker sends the user a link: `x-github-client://openRepo/https://github.com/victim-org/project-a?pr=1`.
4. User clicks the link (e.g., from an email or webpage). Desktop's `parseAppURL` parses it as `open-repository-from-url` [1](#0-0) ; `dispatchURLAction` routes to `openRepositoryFromUrl` → `openPullRequestFromUrl` [5](#0-4) .
5. `getRepositoryFromPullRequest`/`doesRepositoryMatchUrl` matches the user's stale local `project-a` clone by `owner/name` string equality [2](#0-1) , and Desktop fetches and checks out the attacker's PR branch into that unrelated local repository, without verifying that the underlying GitHub repository ID is the same one the user originally cloned.

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1972)
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

**File:** app/src/lib/repository-matching.ts (L73-118)
```typescript
export function repositoryMatchesRemote(
  gitHubRepository: GitHubRepository,
  remote: IRemote
): boolean {
  return (
    urlMatchesRemote(gitHubRepository.htmlURL, remote) ||
    urlMatchesRemote(gitHubRepository.cloneURL, remote)
  )
}

/**
 * Check whether or not a GitHub repository URL matches a given remote, by
 * parsing and comparing the structure of the each URL.
 *
 * @param url a URL associated with the GitHub repository
 * @param remote the remote details found in the Git repository
 */
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
