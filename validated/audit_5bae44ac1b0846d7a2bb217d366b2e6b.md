### Title
Silent rewrite of a repository's `origin` remote URL from an untrusted GitHub API `clone_url` field - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl()` in `app/src/lib/stores/updates/update-remote-url.ts` will silently call `gitStore.setRemoteURL()` to rewrite a user's local `origin` remote based solely on the `clone_url` field returned by a GitHub/GHE API repository object, guarded only by a scheme-equality check (`protocolsMatch`) rather than a host/owner/name check. This mirrors the report's broken-invariant pattern: a value the app treats as "safe to trust and propagate" (the mystery box owner mapping / here, the remote URL) is mutated from attacker-influenceable input without the safety checks a reasonable user would expect, silently corrupting state that controls where the user's future `git push`/`fetch` traffic goes.

### Finding Description
`repositoryWithRefreshedGitHubRepository()` in `app/src/lib/stores/app-store.ts` periodically re-fetches the associated GitHub repository via the API and, when the repository is found, calls `updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)`: [1](#0-0) 

`updateRemoteUrl` then decides whether to overwrite the current `origin` remote: [2](#0-1) 

The only checks performed before calling `gitStore.setRemoteURL(...)` are:
1. `protocolsMatch` — compares only the URL **scheme** (`https:` vs `https:`), not the host.
2. `remoteUrlUnchanged` — compares the *current* remote to the *previously cached* `gitHubRepository.cloneURL`, not to the *new* `apiRepo.clone_url`.
3. `!urlsMatch` — the new URL differs from the current remote.

Crucially there is no check that the new `clone_url` still points to the same host or owner/name that the user originally cloned. The `clone_url` value comes directly from the API response for the account's endpoint, which for GitHub Enterprise accounts is an arbitrary server the user configured (`account.endpoint`), and the account/endpoint matching used to select which account's API to query is done purely by **hostname** in `matchGitHubRepository()`: [3](#0-2) 

Because the API response object (`apiRepo`) is treated as fully trusted, any GHE server (or a network path capable of tampering with responses from that server) can return an arbitrary `clone_url` and, provided the scheme matches, GitHub Desktop will silently repoint the user's local `origin` remote to that URL — with no popup, confirmation, or warning to the user. This satisfies "attacker controls ... a GitHub API object ... or a git remote/proxy response" and results in "silent corruption of what the user commits or pushes," since subsequent `git push`/`git fetch` operations will silently target the attacker-controlled remote.

### Impact Explanation
If exploited, a user's future pushes could be silently redirected to an attacker-controlled remote (leaking source code and possibly credentials sent during authentication to that host via the trampoline credential helper, since credential lookup is also keyed by endpoint/host), or future fetches could pull attacker-controlled content into the user's working copy without any visible change in the UI apart from the remote URL field. This is a supply-chain-style compromise of the user's git workflow, achieved with no user action beyond normal periodic repository refresh.

### Likelihood Explanation
The refresh path (`repositoryWithRefreshedGitHubRepository`) is triggered as part of routine background repository refresh flows already present in `app-store.ts`, not requiring unusual user interaction. The attacker only needs control over (or the ability to tamper with) the API responses for the specific GitHub Enterprise endpoint the victim has already added as an account (a scenario listed as valid: "a git remote/proxy response"). No local access, admin rights, or pre-existing malware is required — only that the returned `clone_url` matches scheme but not host. I could not fully confirm from the available index every call site that triggers `repositoryWithRefreshedGitHubRepository` on a routine cadence (index truncated `app-store.ts` matches beyond what was retrievable), so likelihood should be validated in a full checkout of the repo.

### Recommendation
Extend the guard in `updateRemoteUrl` to require that the new `clone_url`'s hostname continues to match the previously-known hostname (or explicitly prompt/warn the user when the API-reported clone URL's host changes), rather than checking scheme equality alone. Any cross-host change to `clone_url` should require explicit user confirmation before `gitStore.setRemoteURL()` is invoked.

### Proof of Concept
Exact reproduction steps and the precise trigger cadence for `repositoryWithRefreshedGitHubRepository` could not be fully verified from the indexed code alone (only partial `app-store.ts` context was retrievable). Conceptually:
1. User adds a GitHub Enterprise account pointing to `https://ghe.example.com` and clones a repo from it, so `gitHubRepository.cloneURL` = `https://ghe.example.com/org/repo.git`.
2. The attacker (controls `ghe.example.com` or can tamper with its responses) changes the `clone_url` field returned by the repository API endpoint to `https://attacker.example.com/org/repo.git` (same `https:` scheme).
3. On the next periodic refresh, `repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl` sees `protocolsMatch = true`, `remoteUrlUnchanged = true` (remote still matches old cached `cloneURL`), `urlsMatch = false` → calls `gitStore.setRemoteURL('origin', 'https://attacker.example.com/org/repo.git')` silently.
4. The user's next `git push`/`fetch` on `origin` now targets `attacker.example.com` without any warning shown in the UI.

Given the incomplete visibility into the full call graph, I recommend a Devin session with full repo access to confirm the exact refresh trigger and validate the PoC end-to-end.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4904-4910)
```typescript
    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
    }

    const ghRepo = await repoStore.upsertGitHubRepository(endpoint, apiRepo)
    const freshRepo = await repoStore.setGitHubRepository(repository, ghRepo)
```

**File:** app/src/lib/stores/updates/update-remote-url.ts (L18-44)
```typescript
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
