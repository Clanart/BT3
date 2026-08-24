### Title
Automatic remote-URL rewrite from unverified GitHub API `clone_url` silently redirects the user's push/fetch target - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
GitHub Desktop automatically rewrites a repository's `origin` remote URL whenever it detects that the associated `GitHubRepository`'s `clone_url` (fetched from the GitHub API/GHES) differs from the locally configured remote, with no user confirmation prompt. This is the same broken-invariant class as the reported `setCluster()` bug: a security‑relevant state mutation (here, the git remote that determines where commits/pushes go) is performed based on an externally supplied value with insufficient authorization/consent gating.

### Finding Description
`updateRemoteUrl` in [1](#0-0)  compares the repository's current default remote URL against `apiRepo.clone_url` — a value returned by the GitHub/GHES API for the associated `GitHubRepository`. If the protocol matches and the remote hasn't been manually changed from what was last seen, the code calls `gitStore.setRemoteURL(...)` and silently rewrites the origin remote: [2](#0-1) .

`setRemoteURL` itself performs no additional validation beyond running `git remote set-url` [3](#0-2) , and the store-level wrapper simply forwards the call without any confirmation or ownership check [4](#0-3) .

The only guard is `protocolsMatch` (same scheme, e.g. https↔https) — there is no check that the **host** or **owner/repo** stays within an expected trust boundary, and no user-facing confirmation dialog is shown before the remote is changed. Because `apiRepo.clone_url` originates from an API response tied to the `GitHubRepository` record (keyed by repo id, endpoint, etc.), any actor who can influence that value — e.g., a compromised/malicious GitHub Enterprise Server the user's account is already registered against, a MITM on the API request path, or a repository owner performing a rename/ownership transfer that the API reports back with an unexpected `clone_url` — can cause Desktop to silently repoint the local `origin` remote to an arbitrary host, with the same owner/name-hostname protocol format, entirely without the user opting in. This is exactly the pattern flagged in the seed report: a state-changing "setter" with no access-control/consent boundary.

### Impact Explanation
Silently changing the `origin` remote redirects the destination of all future `git push` operations (and potentially fetch/pull sources) for the repository without the user's knowledge. A user who believes they are pushing to their trusted GitHub/GHES repository could unknowingly push commits (potentially containing proprietary code or secrets) to an attacker-controlled remote, or start pulling code from an attacker-controlled source that ends up merged/committed locally. This matches the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
The precondition is that the attacker can influence the `IAPIRepository.clone_url` value that Desktop receives for a repository already tracked by the user (e.g., via a malicious/compromised GHES endpoint the user is signed into, or by controlling the API response path). This is a narrower precondition than the classic missing-`onlyOwner` bug (which is exploitable by literally anyone), so likelihood is lower — it requires an untrusted or compromised API/network layer rather than a purely local attacker-repository object. It does not require local/physical access, admin rights, prior malware, or leaked credentials, only control over the GitHub API response for a tracked repository.

### Recommendation
- Before silently rewriting the remote URL, require an explicit user confirmation prompt showing the old vs. new URL, similar to `RepositorySettings`' manual remote-URL change flow [5](#0-4) .
- Strengthen the guard in `updateRemoteUrl` to also compare hostname (not just protocol) against a known-good allowlist (e.g., the account's `endpoint` host) before auto-updating, rejecting silent cross-host rewrites.
- Log/emit a persistent, dismissible notification whenever an automatic remote-URL change occurs so the user has visibility into the mutation.

### Proof of Concept
1. User adds a repository to Desktop backed by a `GitHubRepository` associated with a GitHub Enterprise endpoint (or any endpoint whose API responses can be influenced by an attacker/compromised proxy).
2. Attacker-controlled/compromised API server returns an `IAPIRepository` object for that repo with `clone_url` pointing to an attacker-owned repository on a different host but same protocol scheme (e.g., `https://attacker-mirror.example/owner/repo.git`).
3. On the next repository refresh, `updateRemoteUrl` is invoked with this `apiRepo`; because `protocolsMatch` is true and `remoteUrlUnchanged` is true (the user hasn't manually touched the remote), `!urlsMatch` becomes true, and `gitStore.setRemoteURL('origin', updatedRemoteUrl)` is called automatically, per [2](#0-1) .
4. The user's next `git push` sends commits to the attacker-controlled host without any dialog or warning.

Note: I was unable to fully trace the exact call sites and triggering conditions of `updateRemoteUrl` within `app-store.ts` (only found 3 textual matches, without full surrounding context) due to index size limits — a Devin session with full repo access would be needed to confirm precisely which refresh/background flows invoke this function and under what account/endpoint trust assumptions.

### Citations

**File:** app/src/lib/stores/updates/update-remote-url.ts (L7-20)
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
```

**File:** app/src/lib/stores/updates/update-remote-url.ts (L42-44)
```typescript
  if (protocolsMatch && remoteUrlUnchanged && !urlsMatch) {
    await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
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

**File:** app/src/ui/repository-settings/repository-settings.tsx (L292-313)
```typescript
  private onSubmit = async () => {
    this.setState({ disabled: true, errors: undefined })
    const errors = new Array<JSX.Element | string>()

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
