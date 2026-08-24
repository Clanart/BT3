### Title
Attacker-controlled fork `clone_url` from GitHub PR API is passed unsanitized into `git remote add`, enabling git option/argument injection - (File: `app/src/lib/git/remote.ts`)

### Summary
The `updateStream()` bug is a case of a state-mutating entry point that fails to enforce an invariant (payability) that its callee (`_depositToken()`) requires, letting attacker/user-supplied conditions reach an unguarded code path. The Desktop analog is structurally the same shape: `addRemote()` is the low-level function that ends up executing `git remote add <name> <url>`, but it never enforces the invariant that `<url>` cannot begin with `-` (which would let git interpret it as a command-line switch rather than a positional URL argument). This function is reachable with attacker-controlled data originating from the GitHub REST API's pull-request payload (`head.repo.clone_url`), which is itself reachable from the `x-github-client://openRepo/...?pr=NNN` deep link handler.

### Finding Description
`addRemote()` builds the git invocation directly from caller-supplied strings with no `--` end-of-options marker and no check that the URL does not start with `-`: [1](#0-0) 

This function is invoked from `_findPullRequestBranch()` using `headCloneUrl`, which is taken verbatim from the PR object returned by the GitHub API (`pullRequest.head.repo.clone_url`) whenever Desktop doesn't already have a matching remote for a fork: [2](#0-1) 

The data flow originates from the `x-github-client://openRepo/<url>?pr=<id>` protocol handler (parsed by `parseAppURL`, see `app/test/unit/parse-app-url-test.ts`), which is dispatched to `openPullRequestFromUrl()`. That function calls `this.appStore.fetchPullRequest(url, pr)` and then forwards the PR's `head.repo.clone_url` straight into `_checkoutPullRequest()`: [3](#0-2) 

`_checkoutPullRequest()` forwards `headCloneUrl` unchanged to `_findPullRequestBranch()`: [4](#0-3) 

Unlike the sibling `openRepositoryFromUrl()` path, which explicitly guards the `filepath` parameter against traversal (`isAbsolute` check + `resolveWithin`), there is no equivalent validation anywhere in this pipeline for the `clone_url`/`headCloneUrl` value before it is handed to git as a bare CLI argument: [5](#0-4) 

Other remote-URL-consuming functions in the same file (`setRemoteURL`, `updateRemoteHEAD`) share the identical pattern of passing raw strings as git arguments with no `--` separator: [6](#0-5) 

### Impact Explanation
`fetchPullRequest()`/the GitHub API response is attacker-influenceable data: any user who can get a PR opened against a repository the victim has cloned in Desktop (or who can get the victim to click a crafted `x-github-client://openRepo/...?pr=` deep link pointing at an attacker-controlled fork PR) controls the `head.repo.clone_url` string returned by the API for that PR. If that string begins with `-` it is not guaranteed to be treated as a positional URL by the underlying git binary invoked via `dugite`'s `git()` wrapper — it can instead be parsed as an option to `git remote add`. This is the same class of bug as the well-known `git clone`/`fetch` "URL starting with `-`" argument-injection issues, where a hostile "URL" is actually a smuggled option (e.g. `--upload-pack=<arbitrary program>` in `git clone`/`fetch` context) that git will attempt to execute in place of a transport helper. Even where `remote add` itself may not support a directly exploitable option, the missing invariant (no `--` guard, no leading-`-` rejection) is the same broken-invariant pattern as the payable/allow-listing bug in the report, and it sits directly in the deep-link and PR-checkout code path with no other guard downstream (`git()` in `core.ts` does not appear to insert an end-of-options marker for these calls based on the code reviewed).

### Likelihood Explanation
The trigger requires only: (1) the victim has GitHub Desktop open, and (2) the attacker gets a crafted `x-github-client://openRepo/<repo>?pr=<n>` link opened (via a webpage, chat message, or GitHub UI element) referencing a PR whose head fork's `clone_url` is attacker-shaped, OR the attacker simply opens a PR from a fork whose repo full-name/clone URL can be influenced and waits for the victim to use "Open in Desktop"/"Checkout PR". No local access, admin rights, or pre-existing malware is needed — this matches the required "unprivileged, attacker controls a GitHub API object / deep link" threat model. The main uncertainty is whether GitHub's own repository-naming/URL constraints (enforced server-side when creating a repo) prevent a `clone_url` from ever literally starting with `-`; I could not verify GitHub's server-side URL construction rules from this codebase, so exploitability depends on whether GitHub's API can be coerced (e.g., via API-level repo renames, custom Enterprise Server instances, or proxy tampering) into returning such a string, or whether some other reachable git-URL field (e.g., a custom git remote/proxy response) is under full attacker control.

### Recommendation
Add a hard input-validation guard (reject/escape values starting with `-`, or always pass `--` before the URL argument) in `addRemote()`, `setRemoteURL()`, and any other function in `app/src/lib/git/remote.ts` that forwards externally-sourced URLs to git, mirroring the same defense-in-depth already used for `filepath` in `openRepositoryFromUrl()`. Apply the same treatment anywhere else a GitHub API-provided `clone_url` reaches a raw git argument list (clone, fetch, remote add/set-url).

### Proof of Concept
Conceptual PoC chain (not independently executed, based on static code-flow evidence above):
1. Attacker opens a fork-based pull request against a public repo such that the GitHub API's `head.repo.clone_url` value for that PR is a string beginning with `-` (exact feasibility depends on GitHub server-side URL validation, which is outside this codebase).
2. Attacker sends the victim a link of the form `x-github-client://openRepo/https://github.com/<owner>/<repo>?pr=<id>`.
3. Victim (with Desktop installed and registered as the protocol handler, see `app/src/main-process/main.ts:159-168`) clicks the link.
4. `parseAppURL` → `openRepositoryFromUrl` → `openPullRequestFromUrl` → `_checkoutPullRequest` → `_findPullRequestBranch` → `addRemote(repository, forkRemoteName, headCloneUrl)` executes `git remote add <name> <headCloneUrl>` with the attacker-controlled string passed as a raw, unguarded CLI argument. [1](#0-0) [7](#0-6)

### Citations

**File:** app/src/lib/git/remote.ts (L28-37)
```typescript
/** Add a new remote with the given URL. */
export async function addRemote(
  repository: Repository,
  name: string,
  url: string
): Promise<IRemote> {
  await git(['remote', 'add', name, url], repository.path, 'addRemote')

  return { url, name }
}
```

**File:** app/src/lib/git/remote.ts (L56-64)
```typescript
/** Changes the URL for the remote that matches the given name  */
export async function setRemoteURL(
  repository: Repository,
  name: string,
  url: string
): Promise<true> {
  await git(['remote', 'set-url', name, url], repository.path, 'setRemoteURL')
  return true
}
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
