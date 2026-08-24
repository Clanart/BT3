### Title
Silent local `git remote set-url` rewrite driven by an untrusted GitHub API `clone_url` field allows redirecting pushes/fetches to an attacker-controlled remote - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl` is analogous to the Rigor `Community.sol` bug in structure: a piece of trusted local state (the git remote URL, analogous to the APR that governs future computation) is silently overwritten using a value taken from an external, less-trusted source (the GitHub API's `clone_url` field, analogous to the newly-set APR) without validating that the new value is safe/expected, and the overwrite then drives all future critical operations (interest calc → git push/fetch) using the tainted value.

### Finding Description
`updateRemoteUrl` [1](#0-0)  takes the `clone_url` returned by the GitHub API for a repository and, if a few structural checks pass, calls `gitStore.setRemoteURL(...)`, which executes `git remote set-url` on disk [2](#0-1)  and `app/src/lib/git/remote.ts` `setRemoteURL` (`git remote set-url name url`) [3](#0-2) .

The guard conditions are:
- `protocolsMatch`: only compares the URL **scheme** string (`https:` vs `https:`), never the hostname.
- `remoteUrlUnchanged`: only verifies the *previous* cached `gitHubRepository.cloneURL` still matches the *current* local remote (i.e., the user hasn't manually customized the remote) — it says nothing about the *new* value's host.
- `!urlsMatch`: only prevents a no-op update; it does not restrict what the new value can be.

None of these checks verify that the new `clone_url`'s hostname matches the account's own endpoint/host (e.g., `github.com` or the specific GHE host the user authenticated `account.endpoint` against) as done elsewhere in `matchGitHubRepository` [4](#0-3) . `urlMatchesRemote` [5](#0-4)  only compares the *new* clone_url's host/owner/name to the *current local remote*, not to the trusted account endpoint. Consequently, if the `clone_url` string returned by `api.fetchRepository(owner, name)` [6](#0-5)  points to a different host entirely (e.g., `https://evil.example/owner/name.git`), all guard conditions can still be satisfied and the local remote gets silently rewritten to that attacker-supplied URL.

This is reachable from `repositoryWithRefreshedGitHubRepository`, which is invoked automatically as part of normal repository-refresh flows (e.g., account changes, periodic refresh) without any user prompt, confirmation dialog, or diff shown to the user [7](#0-6) .

### Impact Explanation
If the `clone_url` field of a GitHub API repository response is attacker-influenced (e.g., a compromised/malicious GitHub Enterprise server the user is signed into, a malicious response served via a MITM/compromised proxy the user is configured to trust, or any code path where an untrusted `IAPIRepository` object reaches this function), Desktop will silently repoint `origin` to an attacker-controlled remote. Subsequent `git push` operations would then push the user's code (and history) to the attacker's server instead of the intended one, and `git fetch`/`git pull` would ingest attacker-controlled content into the working tree presented to the user as "up to date" with the real project — this is exactly the "silent corruption of what the user commits or pushes" class called out as valid impact. Because Desktop's trampoline credential helper resolves credentials by hostname (`findGitHubTrampolineAccount`/`findGenericTrampolineAccount`) [8](#0-7) , if the attacker's host coincidentally matches a known account endpoint or a generic-credential host the user has previously stored, credentials could also be sent to the wrong destination.

### Likelihood Explanation
The main uncertainty (not fully verifiable from the indexed code alone) is how strictly `api.fetchRepository`/`parsedResponse` validates that `clone_url` is well-formed and hosted on the queried endpoint before this object reaches `updateRemoteUrl` — I did not find any such host-pinning validation in `app/src/lib/api.ts` in the reachable snippets, and `IAPIRepository.clone_url` is treated as an opaque string throughout `repository-matching.ts` and `update-remote-url.ts`. The call path itself is triggered during ordinary background refresh (no unusual user action needed), so likelihood is driven entirely by whether an attacker can influence the `clone_url` value returned to `api.fetchRepository` (e.g., compromised/malicious GHE instance the user is signed into, or a network-level tamperer if TLS is not enforced/pinned for that endpoint).

### Recommendation
Before calling `gitStore.setRemoteURL`, validate that the new `clone_url`'s hostname equals the hostname derived from `account.endpoint`/`gitHubRepository.endpoint` (the same check already used in `matchGitHubRepository`), not merely that its protocol matches the current remote's protocol. Reject/ignore any `clone_url` whose host doesn't match the expected, previously-trusted host, and consider surfacing a confirmation to the user when the update would materially change the remote's owner/name/host, similar to how APR-changing operations should snapshot the old rate to compute pending interest correctly before applying the new value.

### Proof of Concept
1. User has GitHub Desktop signed in to a GitHub Enterprise instance `https://ghe.corp.example` (`account.endpoint`), with a repository cloned via `origin` pointing to `https://ghe.corp.example/org/repo.git`.
2. The GHE server (compromised, or a MITM able to tamper with the JSON API response for `/repos/org/repo`) returns `clone_url: "https://ghe.attacker.example/org/repo.git"` while everything else (owner/name/protocol) matches structurally.
3. Desktop performs a routine refresh (`repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl`): `protocolsMatch` is true (both `https:`), `remoteUrlUnchanged` is true (user hasn't touched the remote), `urlsMatch` is false (owner/name look the same but host differs, or repo renamed) — so `gitStore.setRemoteURL('origin', 'https://ghe.attacker.example/org/repo.git')` executes silently.
4. The next time the user runs "Push" from the Desktop UI, `git push` sends the local branch to `ghe.attacker.example` instead of `ghe.corp.example`, with no dialog or warning shown to the user.

**Note:** I could not fully trace the exact validation performed inside `parsedResponse`/`fetchRepository` on the raw `clone_url` string from the network response due to indexing limits on `app/src/lib/api.ts`; a Devin session with full repo access would be needed to confirm whether any additional host-sanity check already exists upstream of this function.

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

**File:** app/src/lib/repository-matching.ts (L28-46)
```typescript
/** Try to use the list of users and a remote URL to guess a GitHub repository. */
export function matchGitHubRepository(
  accounts: ReadonlyArray<Account>,
  remote: string
): IMatchedGitHubRepository | null {
  for (const account of accounts) {
    const htmlURL = getHTMLURL(account.endpoint)
    const { hostname } = URL.parse(htmlURL)
    const parsedRemote = parseRemote(remote)

    if (parsedRemote !== null && hostname !== null) {
      if (parsedRemote.hostname.toLowerCase() === hostname.toLowerCase()) {
        return { name: parsedRemote.name, owner: parsedRemote.owner, account }
      }
    }
  }

  return null
}
```

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

**File:** app/src/lib/stores/app-store.ts (L4874-4914)
```typescript
  private async repositoryWithRefreshedGitHubRepository(
    repository: Repository
  ): Promise<Repository> {
    const repoStore = this.repositoriesStore
    const match = await this.matchGitHubRepository(repository)

    // TODO: We currently never clear GitHub repository associations (see
    // https://github.com/desktop/desktop/issues/1144). So we can bail early at
    // this point.
    if (!match) {
      return repository
    }

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

    const ghRepo = await repoStore.upsertGitHubRepository(endpoint, apiRepo)
    const freshRepo = await repoStore.setGitHubRepository(repository, ghRepo)

    await this.refreshBranchProtectionState(freshRepo)
    return freshRepo
  }
```

**File:** app/src/lib/trampoline/find-account.ts (L20-29)
```typescript
export async function findGitHubTrampolineAccount(
  accountsStore: AccountsStore,
  remoteUrl: string
): Promise<Account | undefined> {
  const accounts = await accountsStore.getAll()
  const parsedUrl = new URL(remoteUrl)
  return accounts.find(
    a => new URL(getHTMLURL(a.endpoint)).origin === parsedUrl.origin
  )
}
```
