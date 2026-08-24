## Analysis

The Solidity report's core defect is a **partial state-migration bug**: a function moves *some* linked resources (`bw`/collateral tokens) while leaving others (`black`/`white` tokens) behind, silently breaking an invariant that a later function (`withdraw`) depends on. The strongest structural analog I found in this codebase is `updateRemoteUrl`, which — as part of a background/periodic "refresh" migration of GitHub repository metadata — silently rewrites the local git remote URL based on server-supplied data, without user confirmation, based on a same-hostname-only match.

### Title
Silent, unconfirmed rewrite of a repository's remote URL from Enterprise API data - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl` is invoked automatically by `repositoryWithRefreshedGitHubRepository` [1](#0-0)  whenever Desktop refreshes a repository's GitHub association (e.g. after an account change, per `refreshSelectedRepositoryAfterAccountChange` [2](#0-1) ). It compares the locally configured remote URL to the `clone_url` returned by `api.fetchRepository(owner, name)` and, if certain heuristics pass, silently calls `gitStore.setRemoteURL(...)` to change the remote — with no user prompt or confirmation.

### Finding Description
The account/repository is matched purely by **hostname** in `matchGitHubRepository` [3](#0-2) , then `owner`/`name` are parsed straight from the existing (already-trusted) remote URL, and the corresponding account's API is queried for that repo: `api.fetchRepository(owner, name)` [4](#0-3) .

The returned `apiRepo.clone_url` then flows into `updateRemoteUrl`: [5](#0-4) 

The guard conditions are:
- protocol of old vs new URL must match,
- the *current* remote URL must still equal the previously cached `gitHubRepository.cloneURL` (i.e., the user hasn't manually customized the remote),
- the new URL differs from the current one.

If all hold, Desktop calls `gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)` with **no user interaction**. The value comes entirely from the account's API response (`apiRepo.clone_url`), which — for any GitHub Enterprise Server account the user has added (a legitimate but attacker-influenceable trust boundary; GHES admin, compromised server, or MITM'd HTTP(S) Enterprise endpoint) — is fully attacker-controlled server data. Nothing validates that the new `clone_url`'s host/owner corresponds to a "sane" migration (e.g., a repo rename); the only invariant enforced is protocol-equality and "was it previously the cached clone URL," which is trivially satisfiable on first refresh after account signup, since the cached `cloneURL` is exactly what was fetched from the same (attacker-influenced) API in the first place.

This is directly analogous to the Solidity bug: a "delegate"-style migration path moves one piece of linked state (the remote URL) based on partially-trusted external input, without checking that the overall invariant ("the remote a user believes they're pushing to/pulling from is the one they set up") still holds — and the function that depends on that invariant (`git push`/`git fetch` via the rewritten remote) will now silently operate against attacker infrastructure.

### Impact Explanation
If exploited, a user's `origin` (or other default) remote is silently repointed to an attacker-supplied `clone_url`. Subsequent `git fetch`/`pull` could pull attacker-controlled objects into the user's working directory (supply-chain risk), and subsequent `git push` could send the user's commits (and, depending on push URL scheme, credentials via the credential helper flow) to an attacker-controlled endpoint — this matches "silent corruption of what the user commits or pushes" and potential credential exfiltration via a git remote controlled by the attacker.

### Likelihood Explanation
This requires the user to have an Enterprise Server (or similarly attacker-reachable) account configured whose API responses are attacker-influenced (malicious/compromised GHES instance, or a MITM able to tamper with that Enterprise API traffic), which is a real "GitHub API object controlled by attacker" scenario named in the valid-impact criteria rather than local/physical access or leaked credentials. The refresh path runs automatically in normal app usage (account change, periodic repo state refresh), requiring no unusual user action beyond having previously added the malicious/compromised Enterprise endpoint as an account and having a repository whose remote still matches the last-known `cloneURL`.

### Recommendation
- Require explicit user confirmation before rewriting an existing remote URL based on API data, rather than doing it silently.
- Additionally validate that the new `clone_url` hostname is consistent with the account's configured endpoint (not just protocol-equality) before considering the rewrite "unchanged/expected."
- Consider logging/telemetry and a visible notification when a remote URL is auto-updated, so users can detect unexpected changes.

### Proof of Concept
I was not able to fully trace every automatic call site that triggers `repositoryWithRefreshedGitHubRepository` in the periodic-refresh path (grep showed 38 references in `app-store.ts`, and I could not exhaustively confirm timing/triggers such as polling intervals within the available iterations). The concrete mechanism shown above (`updateRemoteUrl`'s guard logic and its unconditional call to `setRemoteURL`) is confirmed by code and by the existing unit tests at `app/test/unit/stores/updates/update-remote-url-test.ts` [6](#0-5) , which demonstrate that supplying a different `clone_url` from the "API" causes the local remote to be silently rewritten. A full end-to-end PoC (standing up a malicious Enterprise API endpoint and confirming automatic, unprompted invocation during normal app idle/refresh cycles) would require running the app and is best validated by a Devin session with full repo/runtime access.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4887-4890)
```typescript
    const { account, owner, name } = match
    const { endpoint } = account
    const api = API.fromAccount(account)
    const apiRepo = await api.fetchRepository(owner, name)
```

**File:** app/src/lib/stores/app-store.ts (L4904-4907)
```typescript
    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
    }
```

**File:** app/src/lib/stores/app-store.ts (L4916-4933)
```typescript
  /**
   * Refreshes the GitHub repository information for the currently selected
   * repository when the active account changes. This ensures that permission
   * information is updated after signing in/out.
   */
  private async refreshSelectedRepositoryAfterAccountChange() {
    const repository = this.selectedRepository

    if (repository === null || repository instanceof CloningRepository) {
      return
    }

    if (!isRepositoryWithGitHubRepository(repository)) {
      return
    }

    await this.repositoryWithRefreshedGitHubRepository(repository)
  }
```

**File:** app/src/lib/repository-matching.ts (L29-46)
```typescript
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

**File:** app/src/lib/stores/updates/update-remote-url.ts (L7-44)
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
```

**File:** app/test/unit/stores/updates/update-remote-url-test.ts (L68-81)
```typescript
  it("updates the repository's remote url when the github url changes", async t => {
    const { gitHubRepository, gitStore } = await createRepository(
      t,
      apiRepository
    )
    assert(gitStore.currentRemote !== null)

    const originalUrl = gitStore.currentRemote.url
    const updatedUrl = 'https://github.com/my-user/my-updated-repo'
    const updatedApiRepository = { ...apiRepository, clone_url: updatedUrl }
    await updateRemoteUrl(gitStore, gitHubRepository, updatedApiRepository)
    assert.notEqual(originalUrl, updatedUrl)
    assert.equal(gitStore.currentRemote.url, updatedUrl)
  })
```
