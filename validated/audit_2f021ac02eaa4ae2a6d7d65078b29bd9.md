## Analysis

The Sherlock report's core invariant is: *a security/financial-relevant piece of state must be refreshed from source-of-truth before it's used to make a follow-on decision, otherwise stale data silently corrupts the outcome.* Translating that into GitHub Desktop's threat model (attacker controls a GitHub API response), the closest concrete analog I found is in the automatic remote-URL synchronization logic, which trusts an API-provided value and silently rewrites the local git remote without ever asking the user to confirm — the same "unchecked mutation of an authoritative value" pattern as the Sherlock bug.

### Title
Unconfirmed, automatic rewrite of the git `origin` remote URL from GitHub API `clone_url` allows silent push/fetch redirection - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl()` is invoked every time Desktop refreshes a repository's associated GitHub metadata (on repository selection, after every push, after checkout, etc. via `repositoryWithRefreshedGitHubRepository`). It compares the locally configured remote URL to the `clone_url` field of the API response for that repository and, if a few loose heuristics pass, calls `gitStore.setRemoteURL(...)` to silently run `git remote set-url` — with no user prompt, confirmation dialog, or visible notification. [1](#0-0) 

### Finding Description
`repositoryWithRefreshedGitHubRepository` fetches the repository object from the API (`api.fetchRepository(owner, name)`) and, whenever a `gitHubRepository` is already associated, feeds the raw API result straight into `updateRemoteUrl`: [2](#0-1) 

Inside `updateRemoteUrl`, the only checks performed before rewriting the remote are:
- `protocolsMatch`: the scheme (https/ssh) of the old and new URL matches — the **hostname is never checked**.
- `remoteUrlUnchanged`: the *currently configured* remote still matches what Desktop previously cached as `gitHubRepository.cloneURL`.
- `!urlsMatch`: the new API value differs from what's configured. [3](#0-2) 

If all three hold, `gitStore.setRemoteURL()` runs `git remote set-url` directly against the working copy with **no confirmation UI whatsoever**: [4](#0-3) 

Nowhere in this chain is the returned `clone_url` validated against the API endpoint's own host, nor is the user shown any diff/prompt akin to what Desktop does for other remote-URL changes made in Settings. The trust boundary being crossed is: *the JSON body of a `GET /repos/{owner}/{name}` response is treated as fully authoritative for a value (the push/fetch destination) that used to require explicit user action to change.* This is structurally identical to the Sherlock bug's invariant violation — a value that gates a subsequent state-changing operation (interest accrual vs. push/fetch destination) is consumed without validating that it's still trustworthy/unchanged in a safe way.

### Impact Explanation
Since Desktop is regularly configured to talk to self-hosted GitHub Enterprise Server instances (an "attacker controls a git remote/proxy response" scenario per the task's valid-impact list — e.g., a compromised, mis-configured, or MITM'd GHES endpoint, or a malicious admin of that instance), the `clone_url` returned in the API JSON can be set to an arbitrary URL. Because only the *protocol* is checked, an attacker-controlled response of `https://attacker.example/evil.git` will pass validation and Desktop will silently repoint `origin` there. From that point on:
- The next `git push` performed from Desktop sends the user's source code (potentially private/proprietary) to the attacker-controlled host — this is source exfiltration.
- The next `git fetch`/`pull` merges attacker-controlled history/content into the user's working tree without any indication the remote changed — this is "silent corruption of what the user commits or pushes," explicitly listed as valid impact.

No popup, banner, or diff is ever shown to the user; the only trace is the git config, which nobody inspects proactively.

### Likelihood Explanation
This code path runs unconditionally and frequently — on every repository selection (`_selectRepositoryRefreshTasks` → `repositoryWithRefreshedGitHubRepository`), and via `withRefreshedGitHubRepository` used around commit, push, checkout, and revert flows. [5](#0-4) 

The attacker only needs one malicious/compromised API response for a repository the user has already added to Desktop (most plausible for GHES setups, forks, or renamed/transferred repositories) — no local access, no malware, and no unusual user action is required; the corruption happens automatically the next time Desktop does a routine background refresh.

### Recommendation
- Never auto-rewrite `origin` (or any remote) based on API data without an explicit, visible confirmation dialog that shows the old and new URL.
- Validate that the new `clone_url`'s hostname matches the account's configured endpoint hostname (dotcom or GHES), not just the protocol.
- Treat this the same as any other destructive remote-config change and require user consent, mirroring the confirmation flows already used elsewhere in Desktop for similarly consequential actions.

### Proof of Concept
1. Add a GitHub Enterprise Server account in Desktop and clone a repository from it, so `repository.gitHubRepository.cloneURL` and the git `origin` remote both point at `https://ghes.corp.example/org/repo.git`.
2. Compromise/control the response of `GET /api/v3/repos/org/repo` on that GHES instance (e.g., via a malicious/compromised server, or an SSRF/MITM against the enterprise instance) so that `clone_url` is `https://attacker.example/org/repo.git`.
3. Trigger any routine refresh in Desktop (select the repository, or perform any push/checkout) — this calls `repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl`. [6](#0-5) 
4. Because `protocolsMatch` is true and `remoteUrlUnchanged` is true (the user never manually changed the remote), Desktop silently runs `git remote set-url origin https://attacker.example/org/repo.git` with no dialog.
5. The next push from Desktop sends the user's commits to `attacker.example`; the next pull merges attacker-supplied content into the user's branch.

### Citations

**File:** app/src/lib/stores/updates/update-remote-url.ts (L7-45)
```typescript
export async function updateRemoteUrl(
  gitStore: GitStore,
  gitHubRepository: GitHubRepository,
  apiRepo: IAPIRepository
): Promise<void> {
  // I'm not sure when these early exit conditions would be met. But when they are
  // we don't have enough information to continue so exit early!
  if (gitStore.defaultRemote === null) {
    return
  }

  const remoteUrl = gitStore.defaultRemote.url
  const updatedRemoteUrl = apiRepo.clone_url
  const urlsMatch = urlMatchesRemote(updatedRemoteUrl, gitStore.defaultRemote)

  // Verify that protocol hasn't changed. If it has we don't want
  // to alter the protocol in case they are relying on a specific one.
  // If protocol is null that implies the url is a ssh url
  // of the format git@github.com:octocat/Hello-World.git, which
  // can't be parsed by URL.parse. In this case we assume the user
  // manually configured their remote to use this format and we don't
  // want to change what they've done just to be safe
  const parsedRemoteUrl = URL.parse(remoteUrl)
  const parsedUpdatedRemoteUrl = URL.parse(updatedRemoteUrl)
  const protocolsMatch =
    parsedRemoteUrl.protocol !== null &&
    parsedUpdatedRemoteUrl.protocol !== null &&
    parsedRemoteUrl.protocol === parsedUpdatedRemoteUrl.protocol

  // Check if the default remote url has been manually changed from the
  // clone url retrieved from the GitHub API previously
  const remoteUrlUnchanged =
    gitStore.defaultRemote &&
    urlMatchesRemote(gitHubRepository.cloneURL, gitStore.defaultRemote)

  if (protocolsMatch && remoteUrlUnchanged && !urlsMatch) {
    await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
  }
}
```

**File:** app/src/lib/stores/app-store.ts (L4886-4907)
```typescript

    const { account, owner, name } = match
    const { endpoint } = account
    const api = API.fromAccount(account)
    const apiRepo = await api.fetchRepository(owner, name)

    if (apiRepo === null) {
      // If the request fails, we want to preserve the existing GitHub
      // repository info. But if we didn't have a GitHub repository already or
      // the endpoint changed, the skeleton repository is better than nothing.
      if (endpoint !== repository.gitHubRepository?.endpoint) {
        const ghRepo = await repoStore.upsertGitHubRepositoryFromMatch(match)
        return repoStore.setGitHubRepository(repository, ghRepo)
      }

      return repository
    }

    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
    }
```

**File:** app/src/lib/stores/app-store.ts (L8285-8306)
```typescript
  private async withRefreshedGitHubRepository<T>(
    repository: Repository,
    fn: (repository: Repository) => Promise<T>
  ): Promise<T> {
    let updatedRepository = repository
    const account: Account | null = getAccountForRepository(
      this.accounts,
      updatedRepository
    )

    // If we don't have a user association, it might be because we haven't yet
    // tried to associate the repository with a GitHub repository, or that
    // association is out of date. So try again before we bail on providing an
    // authenticating user.
    if (!account) {
      updatedRepository = await this.repositoryWithRefreshedGitHubRepository(
        repository
      )
    }

    return fn(updatedRepository)
  }
```

**File:** app/src/lib/stores/git-store.ts (L1533-1543)
```typescript
  /** Changes the URL for the remote that matches the given name  */
  public async setRemoteURL(name: string, url: string): Promise<boolean> {
    const wasSuccessful =
      (await this.performFailableOperation(() =>
        setRemoteURL(this.repository, name, url)
      )) === true
    await this.loadRemotes()

    this.emitUpdate()
    return wasSuccessful
  }
```
