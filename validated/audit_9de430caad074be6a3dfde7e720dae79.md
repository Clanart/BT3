### Title
Deep link (`x-github-client://openRepo/…?pr=N`) silently checks out an attacker-chosen PR ref onto the user's already-open local repository without confirmation - ([File: app/src/ui/dispatcher/dispatcher.ts])

### Summary
The reported Pontoon bug is a broken-invariant class: a privileged action (unapprove) is executed against an object (a translation ID) that the acting user does not own, because the server trusts a client-supplied ID without checking that it belongs to the caller's authorized scope. The Desktop analog is the `x-github-client://openRepo/...&pr=N` deep-link handler: the app trusts attacker-supplied `url`/`pr` parameters to resolve a Pull Request from the GitHub API and then trusts the API's `head`/`base` `clone_url` fields — not the original link's target — to pick which of the user's *already open, unrelated* local repositories to mutate, and immediately checks out that PR's ref without any confirmation prompt.

### Finding Description
`parseAppURL` accepts an `openRepo` deep link with an unauthenticated `pr` query parameter and passes it straight through as `IOpenRepositoryFromURLAction` [1](#0-0) .

`openRepositoryFromUrl` -> `openPullRequestFromUrl` then calls `appStore.fetchPullRequest(url, pr)` with the attacker-controlled `url`/`pr`, and once a PR object is returned, resolves the *target repository* not from the link's `url`, but from `getRepositoryFromPullRequest`, which scans the user's already-tracked local repositories and matches on the PR's `head`/`base` `clone_url` against either the repo's `origin` **or** its `upstream/parent` URL [2](#0-1) .

Because matching is done against `origin` OR `upstream`, any repository the user has open whose upstream happens to equal the PR's base repo will be selected — even though the link's own `url` parameter pointed somewhere else entirely (e.g., an attacker's fork). Once a match is found, the code immediately calls `_checkoutPullRequest` with the fully attacker/API-controlled `pullRequest.head.repo.owner.login`, `clone_url`, and `ref`, with no popup, confirmation dialog, or comparison against the link's original `url` [3](#0-2) . The whole flow is reachable purely by a user clicking a link (`handleAppURL`/protocol-launcher registration) [4](#0-3) .

The existing guard that *does* exist in this code path — `resolveWithin`/`isAbsolute` checks — only protects the optional `filepath` "show in folder" step [5](#0-4) ; it does not protect the repository-selection or checkout step, so it does not stop this issue.

### Impact Explanation
A single click on a crafted link can cause Desktop to silently fetch a remote ref and check it out into a repository the user already has open — without the user asking to view that specific PR/fork and without any confirmation UI. This can silently corrupt what the user subsequently commits or pushes (they may keep working, unaware their working tree now tracks an attacker-chosen branch/ref from an arbitrary fork), matching the "silent corruption of what the user commits or pushes" impact class.

### Likelihood Explanation
Requires the victim to click a single `x-github-client://openRepo/...?pr=N` link (or open it via CLI/browser "Open in Desktop" flow) while having a matching repository already open in Desktop; no local access, malware, or leaked credentials are needed. The `pr` value is validated only as `/^\d+$/`, and there is no confirmation dialog gating the resulting checkout.

### Recommendation
- Require an explicit confirmation dialog before checking out a PR ref reached via `openRepositoryFromUrl`, showing the resolved owner/clone URL/ref to the user.
- In `getRepositoryFromPullRequest`/`doesRepositoryMatchUrl`, require that the resolved repository's match also be consistent with the link's original `url` parameter, not solely the API-returned `clone_url` values, and stop matching against `upstream` as an implicit trust anchor for arbitrary PR targets.

### Proof of Concept
1. Attacker crafts `x-github-client://openRepo/https://github.com/attacker/fork?pr=1` where PR #1 in `attacker/fork` is opened against (or its head targets) a popular upstream project the victim has cloned in Desktop, with `attacker/fork` set as the head repo/ref.
2. Victim clicks the link (e.g., embedded on a web page or in a chat message).
3. `parseAppURL` -> `openRepositoryFromUrl` -> `openPullRequestFromUrl` fetches the PR, `getRepositoryFromPullRequest` matches the victim's already-open upstream repository via `upstreamRepoUrl`, and `_checkoutPullRequest` checks out the attacker's fork ref into that repository with no confirmation shown to the user.

**Note on completeness:** I was not able to inspect the implementation of `_checkoutPullRequest` in `app/src/lib/stores/app-store.ts` (only its call site) within the available context, so I cannot fully confirm whether any confirmation/notification is surfaced elsewhere in that function before the checkout is applied. If such a confirmation exists downstream, it would reduce (but likely not eliminate, since the repository-selection mismatch itself is unguarded) the severity of this finding.

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1875-1938)
```typescript
  /**
   * Find an existing repository that can be used for checking out
   * the passed pull request.
   *
   * This method will try to find an opened repository that matches the
   * HEAD repository of the PR first and if not found it will try to
   * find an opened repository that matches the BASE repository of the PR.
   * Matching in this context means that either the origin remote or the
   * upstream remote url are equal to the PR ref repository URL.
   *
   * With this logic we try to select the best suited repository to open
   * a PR when triggering a "Open PR from Desktop" action from a browser.
   *
   * @param pullRequest the pull request object received from the API.
   */
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

**File:** app/src/main-process/main.ts (L238-295)
```typescript
async function handleCommandLineArguments(argv: string[]) {
  const args = parseCommandLineArgs(argv, {
    boolean: ['protocol-launcher'],
  })

  // Desktop registers it's protocol handler callback on Windows as
  // `[executable path] --protocol-launcher "%1"`. Note that extra command
  // line arguments might be added by Chromium
  // (https://electronjs.org/docs/api/app#event-second-instance).

  if (__WIN32__ && args['protocol-launcher'] === true) {
    // On Windows we'll end up getting called with something like
    // `--protocol-launcher --allow-file-access-from-files x-github-client://..`
    // which minimist naturally interprets as
    // `--allow-file-access-from-files=x:/github-client`. This is due to
    // Chromium's hot take on parsing command line arguments, see:
    // https://github.com/electron/electron/issues/20322#issuecomment-534137321
    // So while we could add '--allow-file...' as a boolean we can't know for
    // sure that Chromium won't add more switches later on which is why we have
    // to resort to looking through all arguments looking for something that
    // appears to be an app url.
    const prefixes = Array.from(possibleProtocols, p => `${p}://`)
    const matchingUrl = argv.find(arg => {
      if (prefixes.some(p => arg.startsWith(p))) {
        try {
          new URL(arg)
          return true
        } catch (e) {
          log.error(`Unable to parse argument as URL: ${arg}`)
        }
      }
      return false
    })

    if (matchingUrl) {
      handleAppURL(matchingUrl)
    } else {
      log.error(`Encountered --protocol-launcher without app url`)
    }
    // If --protocol-launcher is present we always want to bail and not
    // risk a smuggled cli switch
    return
  }

  if (typeof args['cli-open'] === 'string') {
    handleCLIAction({ kind: 'open-repository', path: args['cli-open'] })
  } else if (typeof args['cli-clone'] === 'string') {
    handleCLIAction({
      kind: 'clone-url',
      url: args['cli-clone'],
      branch:
        typeof args['cli-branch'] === 'string' ? args['cli-branch'] : undefined,
    })
  }

  return
}

```
