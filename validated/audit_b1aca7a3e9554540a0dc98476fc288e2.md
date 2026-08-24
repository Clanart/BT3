### Title
Automatic remote-URL rewrite trusts unvalidated `clone_url` from the GitHub API, allowing a malicious/compromised GitHub host to silently redirect future pushes/fetches - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
This is the same broken-invariant pattern as the Hats Protocol bug: an authoritative source (the toggle contract / here, the GitHub API response for a repository) is trusted to overwrite locally-persisted state (`hat.config` / here, the git `origin` remote URL) without validating that the new value is safe, based only on a "did the local value change from what we last cached" comparison rather than validating the *destination* itself.

### Finding Description
`repositoryWithRefreshedGitHubRepository` in `app/src/lib/stores/app-store.ts` periodically re-fetches repository metadata from the GitHub API and, if the local `gitHubRepository` model is already known, calls `updateRemoteUrl`: [1](#0-0) 

`updateRemoteUrl` (`app/src/lib/stores/updates/update-remote-url.ts`) decides whether to silently rewrite the local git `origin` remote based only on: (1) protocol equality, (2) whether the *current* remote still textually matches the *previously cached* `gitHubRepository.cloneURL`, and (3) whether the new `apiRepo.clone_url` differs from the current remote: [2](#0-1) 

If all three hold, it calls `gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)` — i.e. it runs `git remote set-url origin <apiRepo.clone_url>` with **no user confirmation** and **no validation that the new URL points to a "safe" or expected host**. The only guard, `urlMatchesRemote`/`urlsMatch` (`app/src/lib/repository-matching.ts`), merely parses hostname/owner/name out of both URL strings and compares them to each other — it never restricts the new URL to the account's own trusted endpoint domain: [3](#0-2) 

Because `apiRepo.clone_url` is taken verbatim from the JSON body returned by `api.fetchRepository(owner, name)`, a malicious or compromised GitHub Enterprise host (or a man-in-the-middle in front of a self-hosted GHES instance) that the user is authenticated against can return an arbitrary `clone_url` value — including one pointing to a completely different, attacker-controlled host — and Desktop will happily write it into the user's `.git/config` as long as the user hasn't manually customized their remote and the URL scheme (https/ssh) is unchanged. This is exactly the toggle-address analog: the "authority" (API host) is trusted to push new state into local persisted config without the change being checked against any independent invariant (e.g., "does this still point to the same host the account belongs to").

### Impact Explanation
Once the remote is silently rewritten, all subsequent `git push`/`git fetch`/`git pull` from Desktop (and any credential material sent via the trampoline credential helper, see `app/src/lib/trampoline/trampoline-credential-helper.ts`) target the attacker-controlled endpoint instead of the real repository. This satisfies the "silent corruption of what the user commits or pushes" and potentially "credential/token exfiltration" impact categories: a user's future commits could be pushed to (and thus disclosed to, or silently dropped by) an attacker-controlled remote, and generic-git-auth credentials for that host could be requested/leaked to it, all without any dialog comparable to the explicit, user-confirmed flow in `RepositorySettings.onSubmit` (`app/src/ui/repository-settings/repository-settings.tsx:296-313`), which is the only place remote URL changes are normally expected to require explicit user action.

### Likelihood Explanation
Exploitation requires the victim to be signed into a GitHub host (typically a GitHub Enterprise Server instance) that is attacker-controlled or compromised — this fits the in-scope "attacker controls ... a GitHub API object" primitive. The trigger path (`repositoryWithRefreshedGitHubRepository`) runs automatically on repository selection, account refresh, and periodic background sync (`_selectRepositoryRefreshTasks`, `refreshSelectedRepositoryAfterAccountChange`), so no unusual user interaction is required beyond normal use of Desktop against that host.

### Recommendation
Before calling `gitStore.setRemoteURL` in `updateRemoteUrl`, validate that the new `clone_url`'s hostname matches the account's own trusted API/HTML endpoint hostname (the same check `matchGitHubRepository` uses via `getHTMLURL(account.endpoint)`), and/or require explicit user confirmation (similar to the `UpstreamAlreadyExists` dialog pattern already used for upstream remotes) before silently changing `origin`.

### Proof of Concept
1. Sign in to a malicious/compromised GitHub Enterprise Server endpoint in Desktop and clone/add a repository whose remote is `https://ghe.evil-or-compromised.example/org/repo.git`.
2. Leave the remote untouched (so it still matches the cached `gitHubRepository.cloneURL`).
3. Have the (malicious/compromised) server return, for `GET /api/v3/repos/org/repo`, a JSON body with `"clone_url": "https://attacker.example/whatever/malicious.git"` (any host, as long as the scheme is `https`).
4. Trigger a refresh path that calls `repositoryWithRefreshedGitHubRepository` (e.g. reselect the repository, or wait for the periodic account/GitHub-repository refresh).
5. Observe that `app/src/lib/stores/updates/update-remote-url.ts` rewrites `origin` to `https://attacker.example/whatever/malicious.git` with no dialog, and the next push/fetch/pull goes to the attacker's server.

Note: I was not able to fully trace every scheduling trigger of `repositoryWithRefreshedGitHubRepository` (e.g. exact background-fetch cadence) within the indexed code, so the precise frequency/timing of automatic re-triggering could not be fully confirmed from the available snippets.

### Citations

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
