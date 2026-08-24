## Analysis

The AbraNFT bug's core pattern is: a party with limited, "update" privileges (the lender) can quietly rewrite a security-relevant parameter after the fact, causing a check that used to require a real event ("borrower is late" / "collateral lost value") to trivially pass and hand over an asset the party shouldn't yet control.

The closest Desktop analog is the automatic remote-URL rewrite driven by GitHub API data in `updateRemoteUrl`.

### Title
Silent, unconfirmed rewrite of a repository's `origin` remote from an untrusted GitHub API response can redirect a user's future commits/pushes - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
When Desktop refreshes the GitHub metadata for a repository, it fetches `apiRepo` from the API and, if a set of heuristic string checks pass, calls `gitStore.setRemoteURL(...)` to silently overwrite the user's `origin` remote with `apiRepo.clone_url` — with no user prompt, confirmation dialog, or verification that the "new" repository is actually the same underlying repository (e.g. by numeric repo id).

### Finding Description
`repositoryWithRefreshedGitHubRepository` matches the local repository to a GitHub repo purely by `owner/name` extracted from the existing remote, then calls the API and unconditionally feeds the result into `updateRemoteUrl`: [1](#0-0) 

`updateRemoteUrl` then applies a purely heuristic set of checks — same URL-parsed protocol, and that the *current* remote still string-matches the *previously cached* `cloneURL` — before overwriting the remote: [2](#0-1) 

None of these checks validate repository identity continuity (e.g. a stable numeric id); they are all derived from string parsing of `apiRepo.clone_url`, which is data returned by the GitHub API endpoint associated with the account (`api.fetchRepository(owner, name)` in `app-store.ts:4890`). If that API response is attacker-influenced — a compromised or MITM'd GitHub Enterprise Server, a malicious captive-portal/corporate proxy terminating TLS, or a spoofed API endpoint reachable via the account's configured endpoint — the attacker fully controls `clone_url`, `owner`, and `name` for the query. As long as the crafted `clone_url` keeps the same URL scheme (`https`/`ssh`) as the existing remote, `protocolsMatch` and `urlsMatch`/`remoteUrlUnchanged` are trivially satisfiable, and `gitStore.setRemoteURL()` is called with no user visibility.

The actual mutation goes straight to git: `setRemoteURL` runs `git remote set-url <name> <url>` directly against the working copy: [3](#0-2) 

This mirrors the loan-parameter bug's shape: a routine "keep metadata in sync" update path is trusted to silently rewrite a security-relevant value (the destination the user's next `git push` goes to) based on unauthenticated, attacker-reachable input, with the guard rails being string heuristics rather than a hard identity check.

### Impact Explanation
If exploited, the next time the user runs `git push` (or any push through Desktop), their commits are silently sent to a repository the attacker controls rather than the one the user believes they are working with. Because the hostname is preserved by the check, Desktop's credential helper will still supply the user's real credentials/token for that host to the attacker-designated repository path, satisfying "silent corruption of what the user commits or pushes." This can also be leveraged to exfiltrate otherwise-private source code the user pushes, believing it's going to their real repo.

### Likelihood Explanation
This requires the attacker to control (or MITM) responses from the GitHub/GHES API endpoint associated with the user's account — a non-trivial but explicitly in-scope primitive per the task's allowed attacker surfaces ("a GitHub API object ... or a git remote/proxy response"). No additional user action beyond Desktop's normal background repository refresh (which happens automatically, e.g. on repository selection) is required; the rewrite is unprompted and silent, unlike the "unnatural user steps" that are excluded.

### Recommendation
- Require identity continuity before auto-updating a remote (e.g. verify the API-returned repository's persistent numeric `id` matches the previously cached `GitHubRepository.dbID`/upstream id, not just string URL matching).
- Surface an explicit, dismissible confirmation to the user before silently changing `origin`'s URL ("This repository appears to have moved to X — update your remote?").
- At minimum, log/telemetry the change and provide an easy way to detect/rollback silently-applied remote URL changes.

### Proof of Concept
1. User has GitHub Desktop configured against a GitHub Enterprise Server account, with a repository cloned and `origin` pointing to `https://ghe.corp/team/real-repo.git`, associated in Desktop's DB with GitHub repo id `123`.
2. Attacker gains MITM/compromise of the GHES API path used by `api.fetchRepository('team', 'real-repo')` in `repositoryWithRefreshedGitHubRepository` (`app/src/lib/stores/app-store.ts:4890`).
3. Attacker's crafted API response sets `clone_url: 'https://ghe.corp/attacker/evil-repo.git'` (same host/protocol).
4. On the next repository refresh (e.g. selecting the repo, or a background poll), `updateRemoteUrl` (`app/src/lib/stores/updates/update-remote-url.ts:7-45`) evaluates `protocolsMatch = true`, `remoteUrlUnchanged = true` (against the cached prior `cloneURL`, itself sourced from the same attacker-controlled endpoint on first association), `urlsMatch = false` → calls `gitStore.setRemoteURL('origin', 'https://ghe.corp/attacker/evil-repo.git')`.
5. `setRemoteURL` executes `git remote set-url origin https://ghe.corp/attacker/evil-repo.git` (`app/src/lib/git/remote.ts:56-64`) with no dialog shown to the user.
6. The user's next push in Desktop silently sends their commits to `attacker/evil-repo` using their own valid GHES credentials.

### Citations

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
