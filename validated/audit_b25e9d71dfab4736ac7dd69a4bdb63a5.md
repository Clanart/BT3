### Title
Silent, unconfirmed rewrite of a repository's git remote URL based on attacker-influenced GitHub API data - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
GitHub Desktop contains a background routine, `updateRemoteUrl`, that automatically calls `git remote set-url` on a user's repository whenever the GitHub API's `clone_url` for the associated `GitHubRepository` no longer matches the locally configured remote — with **no user prompt, notification, or confirmation dialog**. Because the trigger value (`apiRepo.clone_url`) is server-supplied API data tied to a repository object that can change (e.g., after a rename, transfer, or attacker-controlled repository metadata returned through the API/proxy path), this function represents an unprompted, invisible mutation of the destination that all future `git push`/`git fetch` operations will target. [1](#0-0) 

### Finding Description
`updateRemoteUrl` compares the repository's current default remote URL against `apiRepo.clone_url` retrieved from the GitHub API. If the protocol matches, the remote hasn't been manually diverged from what GitHub Desktop previously cached (`gitHubRepository.cloneURL`), and the URLs differ, it silently invokes `gitStore.setRemoteURL(...)`, which shells out to `git remote set-url`: [2](#0-1) 

That in turn calls the low-level git wrapper directly, with no UI confirmation step: [3](#0-2) [4](#0-3) 

Compare this to the equivalent user-facing flow in Repository Settings, where a URL change only happens after the user explicitly edits the field and clicks "Save": [5](#0-4) 

The "guard" conditions in `updateRemoteUrl` (protocol match, `remoteUrlUnchanged`) are weak:
- They only compare hostname/owner/name via `urlMatchesRemote`/`parseRemote` — they do not verify that the *new* URL is safe, only that the *old* one hadn't been hand-edited. [6](#0-5) 
- Nothing validates that `clone_url` still points to a repository the user actually intends to push to (e.g., after a repo transfer/rename where GitHub returns a different owner/name that still round-trips through the same hostname and protocol).
- There is no dialog, toast, or log-visible warning shown to the user before the remote is rewritten — the change happens as a side effect of a routine metadata refresh.

This function is wired into `AppStore` (three call sites), part of the background repository-refresh/metadata-sync pipeline rather than any explicit user action, meaning it executes without the user clicking anything. I was not able to fully trace the exact call-site trigger (background poll vs. explicit refresh) within the available iterations, so the precise cadence/frequency of invocation is unconfirmed and should be verified directly in `app/src/lib/stores/app-store.ts`.

### Impact Explanation
If an attacker can influence the `clone_url` value returned for a `GitHubRepository` object that Desktop already associates with a local clone (for example, via a compromised/spoofed API response through a MITM'd proxy, or by controlling a repository that gets renamed/transferred to redirect its `clone_url`), Desktop will **silently repoint the user's `origin` remote** to the attacker-controlled URL. Because this happens without any user-visible confirmation, subsequent:
- `git push` operations from that user could send commits/history to the attacker's endpoint, and
- `git fetch`/`pull` operations could pull attacker-supplied refs into the user's working repo,

both of which fall squarely within "silent corruption of what the user commits or pushes" from an attacker-controlled GitHub API object — the exact class of impact this report's method calls for.

### Likelihood Explanation
Exploitation requires the attacker to control or manipulate the `clone_url` field of a `GitHubRepository` API object that Desktop has cached for a tracked repository (e.g., via API tampering, a compromised GitHub Enterprise instance, or a malicious/compromised proxy sitting between Desktop and the API). This does not require local access, admin rights, or prior malware — it only requires the attacker to influence server responses reaching the client, consistent with the allowed "GitHub API object" attacker model. However, exploitation is somewhat conditional (protocol must match, and the local remote must not already have manually diverged), so likelihood is moderate rather than trivial, and I could not confirm from local code alone how frequently this code path runs or the exact upstream trigger for it.

### Recommendation
- Require explicit user confirmation (a dialog) before silently changing an existing remote's URL as a result of background API metadata sync, mirroring the confirmation flow already present in Repository Settings (`app/src/ui/repository-settings/repository-settings.tsx`).
- Log and surface a visible notification when `updateRemoteUrl` performs an automatic rewrite, so users can detect unexpected remote changes.
- Strengthen the equality check beyond hostname/owner/name matching (e.g., additionally verify via a signed/pinned identifier or repository ID rather than solely a re-derivable URL) to reduce spoofability of the "URLs differ" trigger.

### Proof of Concept
Conceptual (could not execute in this environment — no filesystem/terminal access to run Desktop or forge API responses):
1. Clone a GitHub repository in Desktop so `gitHubRepository.cloneURL` and the `origin` remote both point to `https://github.com/victim/repo.git`.
2. Have Desktop's next metadata refresh receive (via a compromised/spoofed API response) an `IAPIRepository` object for the same tracked repo where `clone_url` is `https://github.com/attacker/repo.git`, keeping the `https` protocol intact.
3. `updateRemoteUrl` evaluates: `protocolsMatch === true`, `remoteUrlUnchanged === true` (local remote still equals the previously cached `cloneURL`), `urlsMatch === false` → condition at [7](#0-6)  is satisfied, and `gitStore.setRemoteURL('origin', 'https://github.com/attacker/repo.git')` executes with no user prompt.
4. The user's next `git push`/`git fetch` in Desktop silently targets `attacker/repo` instead of `victim/repo`.

### Citations

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

**File:** app/src/ui/repository-settings/repository-settings.tsx (L296-313)
```typescript
    if (this.state.remote && this.props.remote) {
      const trimmedUrl = this.state.remote.url.trim()

      if (trimmedUrl !== this.props.remote.url) {
        try {
          await this.props.dispatcher.setRemoteURL(
            this.props.repository,
            this.props.remote.name,
            trimmedUrl
          )
        } catch (e) {
          log.error(
            `RepositorySettings: unable to set remote URL at ${this.props.repository.path}`,
            e
          )
          errors.push(`Failed setting the remote URL: ${e}`)
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
