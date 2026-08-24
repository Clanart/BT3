### Title
Silent, unauthenticated rewrite of a repository's `origin` remote URL from GitHub API data with no event/log or user confirmation - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
GitHub Desktop automatically rewrites a repository's Git remote URL whenever it refreshes GitHub metadata, based solely on the `clone_url` field returned by the GitHub API for the associated repository. This happens silently — no event is emitted, no log entry is written, and no user confirmation dialog is shown — mirroring the reported bug class of "important state changes happen without being logged, allowing malicious changes to go unnoticed."

### Finding Description
`updateRemoteUrl` compares the repository's current `origin` URL against the `clone_url` supplied in an `IAPIRepository` object and, if the protocol matches and the previously-stored `clone_url` still matched the existing remote, calls `gitStore.setRemoteURL(...)` to silently overwrite the local remote: [1](#0-0) 

This function is invoked from `repositoryWithRefreshedGitHubRepository`, which runs during routine background refreshes (e.g., every fetch, every account-change refresh) with the `apiRepo` object fetched straight from the GitHub API: [2](#0-1) 

The only checks performed are:
1. The stored `clone_url` on record must currently match the local remote (`remoteUrlUnchanged`).
2. The URL scheme (`https:` vs `ssh:`) must be unchanged (`protocolsMatch`).
3. The new `clone_url` differs from the current remote.

There is no check that the new host/owner still belongs to a trusted namespace, no diff shown to the user, and critically, no event/notification/log emitted when the rewrite occurs — the change is applied via `gitStore.setRemoteURL` and the UI is only informed indirectly through the general "repository refreshed" state update, not a distinguishable "remote URL changed" signal. This is structurally identical to the reported `MarginCalculator` issue: a function that mutates a security-relevant configuration value (there: `spotShock`; here: the git remote/origin URL) without any accompanying audit trail, so users cannot notice that Desktop has quietly moved where their pushes/fetches go.

### Impact Explanation
The `origin` remote URL is the value GitHub Desktop uses for all subsequent pushes, fetches, and pulls performed through its UI. If an attacker can influence the `clone_url` returned for the associated `IAPIRepository` (the report's valid-impact class explicitly includes "attacker controls...a GitHub API object"), they can retarget the user's remote to an attacker-controlled repository without any visible warning:
- Subsequent pushes from Desktop would silently send the user's commits (and, in an HTTPS remote scenario, their credential/token via the Git credential helper flow) to the attacker's repository instead of the intended one.
- Subsequent fetches/pulls would silently import attacker-controlled history into the user's local repository, which the user might then merge or build on, believing it is the legitimate upstream.

Because no `CollateralDustUpdated`-style event/log analog exists here (no notification, banner, or audit entry), the user has no way to detect that their `origin` was changed until they manually inspect `git remote -v` — this is the direct analog of the original report's "changes...not logged appropriately... could go unnoticed."

### Likelihood Explanation
The guard conditions (`protocolsMatch`, `remoteUrlUnchanged`) are designed for the legitimate "repository was renamed on GitHub.com" case, and they do not verify that the new URL still points to a namespace the user actually intended or trusts — they merely require that the *previous* API-reported `clone_url` matched the existing remote, which is exactly the state right after Desktop itself performed the last legitimate sync. Any path that can supply a crafted `IAPIRepository`/`IAPIFullRepository` response for the tracked repo (e.g., a compromised or malicious GitHub Enterprise endpoint, or a man-in-the-middle on the API response for a GHE server) satisfies these checks and triggers the silent rewrite on the very next background refresh — no user interaction beyond having Desktop open and syncing is required. This matches the report's "attacker enables an undesirable state change that isn't logged" pattern closely, though it does require the attacker to control an API response for the account's endpoint (in scope per the given Valid Impact rubric).

### Recommendation
- Emit a dedicated, loggable event/notification whenever `updateRemoteUrl` (or `gitStore.setRemoteURL` in this automatic-refresh path) changes the `origin` URL, and surface it to the user (e.g., a banner: "Desktop updated your remote URL from X to Y because the repository was renamed").
- Before silently rewriting the remote, additionally verify that the owner/organization portion of the URL is unchanged (not just that the protocol matches), to avoid silently redirecting `origin` to a different owner/org.
- Expand test coverage (`app/test/unit/stores/updates/update-remote-url-test.ts`) to assert that an event/log is produced whenever the remote URL is changed automatically.

### Proof of Concept
1. User has GitHub Desktop signed into a GitHub Enterprise (or dotcom-compatible) endpoint and a local repository whose `origin` remote matches the `clone_url` Desktop last recorded for the associated `GitHubRepository`.
2. Attacker who can influence the `GET repos/{owner}/{name}` API response for that endpoint (e.g., malicious/compromised GHE server, or a MITM against that specific endpoint) returns a response where `clone_url` points to `https://attacker.example/owner/malicious-repo.git`, keeping the same protocol (`https`).
3. On Desktop's next background refresh (fetch, pull, or account refresh) `repositoryWithRefreshedGitHubRepository` runs, calling `updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)`: [3](#0-2) 
4. Because `remoteUrlUnchanged` and `protocolsMatch` are true and `urlsMatch` is false, `gitStore.setRemoteURL('origin', 'https://attacker.example/owner/malicious-repo.git')` executes with no event emitted, no dialog, and no log entry visible in the standard app UI.
5. The user's next "Push" or "Fetch" from the Desktop UI now silently targets `attacker.example`, exfiltrating pushed commits/credentials or pulling attacker-supplied history, with the user unaware the remote ever changed.

Unknown/unverified: whether `gitStore.setRemoteURL` (in `app/src/lib/stores/git-store.ts`) internally logs the change anywhere accessible to the user — I was unable to view that function's body before running out of tool calls, so this should be confirmed by reading `app/src/lib/stores/git-store.ts` and `app/src/lib/git/remote.ts` `setRemoteURL` implementations directly.

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

**File:** app/src/lib/stores/app-store.ts (L4890-4907)
```typescript
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
