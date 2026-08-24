### Title
Automatic origin-remote rewrite from GitHub API `clone_url` lacks destination validation, enabling silent push/fetch redirection - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl` automatically rewrites a tracked repository's `origin` remote URL using the `clone_url` field of the associated `GitHubRepository` API object, with no validation that the new URL still points to the same owner/repo the user originally intended. The only checks performed are that the URL scheme (http vs https vs ssh) is unchanged and that the user hasn't manually customized the remote. An attacker who controls the upstream GitHub API object for a repository the victim has added to Desktop (e.g., by renaming/transferring their own public repository, or via a compromised/malicious GitHub Enterprise Server endpoint) can cause Desktop to silently repoint the victim's `origin` remote to an arbitrary destination on the next background repository refresh, without any user confirmation.

### Finding Description
`repositoryWithRefreshedGitHubRepository` in `app-store.ts` periodically re-fetches the repository from the GitHub/GHES API and unconditionally calls `updateRemoteUrl`: [1](#0-0) 

`updateRemoteUrl` performs the actual rewrite: [2](#0-1) 

The guard conditions are:
1. `protocolsMatch` — only requires that `URL.parse(remoteUrl).protocol === URL.parse(updatedRemoteUrl).protocol` (e.g., `https:` === `https:`), which says nothing about hostname, owner, or repo name.
2. `remoteUrlUnchanged` — only verifies the *current* remote still matches the previously known `cloneURL` for that `GitHubRepository`, i.e., it protects against overwriting a remote the user manually edited, but does not restrict what the *new* URL can be.

Because `apiRepo.clone_url` comes directly from the attacker-influenceable API object — the same repository record that any owner can alter by renaming or transferring their own repo, or that a malicious/compromised GitHub Enterprise Server can return outright — there is no check that `hostname`/`owner`/`name` of the new URL bears any relation to the original. `gitStore.setRemoteURL` then executes `git remote set-url origin <url>` without further validation: [3](#0-2) [4](#0-3) 

Notably, this differs from the `upstream` remote flow (`addUpstreamRemoteIfNeeded` / `UpstreamAlreadyExists` dialog), which explicitly prompts the user before overwriting an existing upstream remote pointing "elsewhere": [5](#0-4) 
No equivalent confirmation exists for the automatic `origin` rewrite performed by `updateRemoteUrl`.

### Impact Explanation
Once the `origin` remote is silently repointed, subsequent user-initiated `git push`/`git fetch` operations will target the attacker-chosen destination while the UI continues to show the repository as the originally added one. This can result in:
- Source code being pushed to a destination the user never approved (silent corruption of what the user pushes).
- Fetch/pull of attacker-controlled history/objects being merged into the user's local branches (silent corruption of what the user later commits on top of).
This matches the "silent corruption of what the user commits or pushes" category in scope. The change requires no local/physical access, no malware, and no unnatural user action — it happens automatically during Desktop's normal background repository refresh.

### Likelihood Explanation
The trigger conditions are attacker-controlled and easily reachable:
- Any user who has ever added/cloned a repository through Desktop where the account endpoint or repository record is later manipulated by its owner (rename, transfer) will have `apiRepo.clone_url` change on the next scheduled refresh.
- For victims using a self-hosted GitHub Enterprise Server account, a compromised or malicious server operator can return an arbitrary `clone_url` for any repository response at any time.
- The scheme-match check is trivially satisfied (`https` stays `https`), and the "unchanged remote" guard is satisfied for any user who hasn't manually hand-edited `origin`, which is the overwhelming majority of users.

### Recommendation
Do not perform automatic, silent updates of the `origin` remote URL based solely on an API-provided `clone_url`. At minimum:
- Validate that the new URL's hostname is unchanged (or restricted to the same account/endpoint the user configured) before applying it automatically.
- Require explicit user confirmation (similar to the existing `UpstreamAlreadyExists` dialog) whenever the destination owner/repo name of the `origin` remote would change, rather than only checking protocol equality.
- Log/surface these automatic remote changes prominently in the UI so users can detect unexpected redirection.

### Proof of Concept
1. Victim clones/adds a public repository owned by the attacker (e.g., `https://github.com/attacker/foo`) into GitHub Desktop; `origin` is set to that URL and `GitHubRepository.cloneURL` is cached as the same.
2. Attacker renames the repository (or, in the GHES case, simply configures their server to answer with a different `clone_url` while keeping the `https` scheme) to `https://github.com/attacker/malicious-mirror`.
3. On Desktop's next background refresh (`repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl`), `protocolsMatch` is true (`https` === `https`) and `remoteUrlUnchanged` is true (user never manually edited `origin`), so:
```ts
await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
```
silently executes `git remote set-url origin https://github.com/attacker/malicious-mirror` with no dialog or confirmation.
4. The victim's next `git push` sends commits to the attacker's repository instead of the originally intended one.

Note: I was not able to fully trace how Desktop's credential/token injection (trampoline/askpass) scopes tokens to hostnames within this index, so I cannot confirm whether this same path could also lead to token exfiltration if the attacker changes the hostname itself (not just owner/repo on the same host); that portion is uncertain and would benefit from further investigation in a full checkout of the repository.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4900-4907)
```typescript

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

**File:** app/src/ui/upstream-already-exists/upstream-already-exists.tsx (L23-26)
```typescript
/**
 * The dialog shown when a repository is a fork but its upstream remote doesn't
 * point to the parent repository.
 */
```
