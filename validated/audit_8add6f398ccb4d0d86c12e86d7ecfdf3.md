### Title
Automatic remote-URL rewriting from GitHub API `clone_url` allows silent redirection of push/fetch to an attacker-controlled repository - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl()` automatically rewrites a repository's local git `origin` remote whenever Desktop refreshes the associated GitHub repository metadata and observes that `apiRepo.clone_url` differs from the last known clone URL. This mirrors the disclosed collateral-manager bug class: a "supported" identifier (there, a collateral type / EToken mapping; here, the trusted GitHub-repository ↔ remote-URL mapping) is silently changed based on external state, and downstream logic (push/fetch operations, and the credential helper that authenticates them) keeps operating against the new identifier without the user consciously re-establishing trust in it.

### Finding Description
`repositoryWithRefreshedGitHubRepository()` in `app/src/lib/stores/app-store.ts` (around lines 4874-4914) periodically re-derives a repository's owner/name via `matchGitHubRepository()` and calls `api.fetchRepository(owner, name)` — a network response entirely controlled by the remote GitHub/GHE host. If a `gitHubRepository` association already exists, the result is passed to: [1](#0-0) 

`updateRemoteUrl()` then compares the *previously cached* `gitHubRepository.cloneURL` against the current local git remote, and the *freshly fetched* `apiRepo.clone_url` against the cached one: [2](#0-1) 

If the protocol matches and the local remote hasn't been manually edited, the function silently calls `gitStore.setRemoteURL()` to point `origin` at whatever URL the API just returned — with no user confirmation, no diff shown, and no verification that the repository at that URL is the *same underlying repository* (e.g. via a stable database/node ID). The matching logic in `repository-matching.ts` (`urlMatchesRemote`, `urlsMatch`) only ever compares hostname/owner/name strings, never a repository identity field: [3](#0-2) 

This is the direct analog of the reported bug: just as `CollateralManager.setEToken()`/`removeCollateral()` changes which real-world asset an EToken maps to without invalidating stale balances or requiring redemption first, `updateRemoteUrl()` changes which real-world git server the trusted local "origin" maps to without any re-validation step, and every subsequent git operation (fetch, push, LFS, submodule handling, and the credential helper) simply trusts the new mapping. The corrupted value is the local `origin` remote URL, and the broken invariant is "the remote a user pushes to/pulls from is the one the user originally configured/trusted," which the app overwrites automatically based on an untrusted network response.

### Impact Explanation
Once the app rewrites `origin` to a different URL, all future `git push`/`git pull`/`git fetch` operations invoked by Desktop go to that new destination — silently corrupting what the user pushes (private code, secrets, commits) by redirecting it to a possibly attacker-controlled repository, without any prompt (`repository-settings.tsx`'s explicit `setRemoteURL` flow, by contrast, requires the user to type the URL themselves). Because credential resolution (`findGitHubTrampolineAccount` in `app/src/lib/trampoline/find-account.ts`) matches purely by host origin, any account bound to that host will have its token supplied to the (still same-host) destination automatically, so this can also result in the user's authenticated token/credentials being used against a repository they never intended to interact with. This satisfies the required impact class: "the result is ... silent corruption of what the user commits or pushes" driven by "a GitHub API object" the attacker controls (the `clone_url` field of the repository API response).

### Likelihood Explanation
The refresh path (`repositoryWithRefreshedGitHubRepository`) runs as part of routine, unattended repository refresh cycles, not as a one-off user action, so the rewrite can occur without any interactive step by the user. The realistic trigger is a "repo-jacking"-style scenario: a repository is renamed (GitHub API then legitimately returns a new `clone_url` for the old owner/name lookup, which is the intended use case this function was built for — see `changelog.json` entry "[Fixed] Update the remote url when a repository's name changes on GitHub - #8590"), and if the old owner/name becomes available for reuse and is reclaimed by a different party, Desktop has no way to distinguish "legitimate rename of my repo" from "someone else now owns this name" because the comparison is purely string-based (owner/name/host), not identity-based (repository ID). This requires no local access, no malware, and no unnatural user action — it is a byproduct of Desktop's normal background repository-refresh behavior combined with an external, attacker-influenceable API response.

### Recommendation
- Only auto-update the remote URL when the newly fetched `apiRepo`'s stable identifier (database `id` / `node_id`) matches the previously stored `gitHubRepository`'s identifier, not just owner/name string equality.
- If the identifier differs (i.e., the name has been reused by a different repository), do not silently rewrite the remote; instead surface a warning/dialog asking the user to confirm before changing where their pushes/fetches go.
- Consider requiring explicit user confirmation for any automatic remote URL change in general, mirroring the manual flow already present in `repository-settings.tsx`.

### Proof of Concept
1. User has Desktop tracking `origin` = `https://github.com/acme/project` with a cached `gitHubRepository` (owner=`acme`, name=`project`, cached `cloneURL` matching origin).
2. The `acme/project` repository is renamed (legitimately) to `acme/project-renamed`; shortly after, the now-available name `acme/project` is reclaimed by an attacker who creates a new, unrelated (possibly malicious) repository at that same owner/name path — a scenario GitHub does not always prevent once a rename redirect expires or a name is otherwise freed.
3. On its next periodic refresh, Desktop calls `matchGitHubRepository()` → still resolves owner=`acme`, name=`project` from the stale cached association, then `api.fetchRepository('acme', 'project')` returns the *attacker's* repository object with its own `clone_url`.
4. `repositoryWithRefreshedGitHubRepository()` passes this to `updateRemoteUrl()` [4](#0-3) , which — since the local remote still textually matches the old cached `cloneURL` and protocols match — calls `gitStore.setRemoteURL()` and rewrites `origin` to the attacker's clone URL, all without any dialog shown to the user.
5. The user's next `git push` from Desktop silently pushes to the attacker's repository instead of their intended one.

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
