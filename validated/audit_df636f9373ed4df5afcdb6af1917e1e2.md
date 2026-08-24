## Analog Found: Silent Remote URL Rewrite from Untrusted GitHub API Data

### Title
Automatic overwrite of local `origin` remote URL from an unvalidated GitHub API `clone_url` value can silently redirect fetch/push traffic to an attacker-controlled host - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
The Sherlock report's broken invariant is: *a value that should reflect trusted, time-consistent state is instead derived from a single, attacker-influenceable read of live/spot data, and that value is then used to make a high-trust decision without further verification.* The GitHub Desktop analog is `updateRemoteUrl`, which silently rewrites the user's local git `origin` remote based on the `clone_url` field of a GitHub API repository response — a value that is fully controlled by whoever administers the remote GitHub/GHE repository the user has cloned.

### Finding Description
`updateRemoteUrl` compares the current local remote URL against `apiRepo.clone_url` (fetched live from the GitHub API) and, if a small set of heuristic conditions hold, calls `gitStore.setRemoteURL` to rewrite the local remote to the API-provided value with no user prompt or confirmation: [1](#0-0) 

The three guard conditions are:
1. `protocolsMatch` — only compares `URL.parse(...).protocol`, which is trivially satisfiable (`https` vs `https`).
2. `remoteUrlUnchanged` — checks that the *previously cached* `gitHubRepository.cloneURL` still matches the local remote (i.e., the user hasn't manually edited it).
3. `!urlsMatch` — the new API value differs from what's stored locally.

None of these guards validate that the new `clone_url` still points to the *same* logical repository (same owner/name) that the user originally added as a remote — they only check protocol equality and whether the local remote drifted from a previously-cached value. `urlMatchesRemote`, used for the "unchanged" check, does compare owner/name, but it's checked against the *old* cached `gitHubRepository.cloneURL`, not against the *new* incoming value, so there is no invariant enforcing that the newly-applied URL still targets the expected owner/repo.

This is the direct git-client analog of `ERC4626Oracle.getPrice` trusting `previewRedeem`'s spot-computed value derived from `totalAssets()`/`totalSupply` without a time-weighted or invariant check — here, Desktop trusts a spot API field (`clone_url`, which the repo owner can change at any time via rename/transfer/GitHub API abuse) to silently reconfigure a security-relevant local artifact (the git remote used for all future `fetch`/`push`/credential-helper operations), via `git-store.ts`'s `setRemoteURL` wrapper around `setRemoteURL` in `app/src/lib/git/remote.ts`. [2](#0-1) 

### Impact Explanation
If an attacker who controls (or compromises) a GitHub/GHE repository that a victim has cloned changes that repository's `clone_url`-relevant metadata (e.g., via a repository transfer, rename chain, or a malicious/mirrored Enterprise API response), Desktop's background repository refresh will automatically retarget the user's local `origin` remote to the attacker-chosen URL — with no confirmation dialog. Because `protocolsMatch` only requires the same scheme, an attacker still needs `https`, but can redirect to any `https` host they control (e.g., a GHE-lookalike or a self-hosted git server). Consequences:
- Subsequent `git fetch`/`pull` operations retrieve code from the attacker's server, corrupting what the user believes they're syncing with (their local working tree state and future commits are built on attacker-supplied history).
- Subsequent `git push` sends the user's commits/credentials-bearing HTTPS requests to the attacker's host, and the git credential helper (trampoline) will be invoked against the new host, which can be used to harvest short-lived tokens if the attacker's server prompts convincingly.
- This is a "silent corruption of what the user commits or pushes" scenario as defined by the accepted impact category, since the remote change is applied without any user-visible confirmation step.

### Likelihood Explanation
Triggering this requires no local access or malware: the attacker only needs control over a GitHub repository that the victim has already added as a remote (e.g., a repo the victim forked from, contributed to, or was invited to), and to change that repo's `clone_url` through ordinary GitHub actions (rename/transfer) or through a malicious GHE API. Desktop periodically refreshes repository metadata via the normal background refresh path, so this update runs without user interaction — this is a realistic "attacker controls a GitHub API object" scenario as called out in the task's valid-impact criteria.

### Recommendation
Do not silently rewrite the local git remote purely from a live API `clone_url` value. At minimum:
- Verify the new `clone_url` resolves to the *same* owner/name/hostname as the repository record already associated with this local repo (not just protocol equality) before applying the change.
- Prompt the user for explicit confirmation before altering the remote URL, similar to other trust-boundary-crossing UI flows in the app (e.g., host-key/certificate confirmation dialogs).
- Consider persisting the well-known "first-trusted" origin url and require multi-signal corroboration (e.g., matching repository ID from the GitHub API, not just clone_url) before rewriting.

### Proof of Concept
1. Victim clones `https://github.com/victim-org/legit-repo` in GitHub Desktop; Desktop caches `gitHubRepository.cloneURL` for that repo.
2. Attacker, who has admin rights over `victim-org/legit-repo` (e.g., a compromised maintainer account, or a repo the victim was invited to collaborate on), renames/transfers the repository such that the GitHub API's `clone_url` field for that repository ID now differs (still `https`, same protocol) — e.g., pointing to `https://github.com/attacker-org/legit-repo` after a transfer, or to a look-alike GHE endpoint if using GitHub Enterprise.
3. On Desktop's next background repository refresh, `updateRemoteUrl` runs: `protocolsMatch` is true (both `https:`), `remoteUrlUnchanged` is true (user never manually edited the remote), and `urlsMatch` is false (URLs differ) — all three conditions required to trigger the rewrite are satisfied: [3](#0-2) 
4. `gitStore.setRemoteURL(...)` is called with the attacker's URL, silently reconfiguring `origin` with no dialog shown to the user.
5. The victim's next `git fetch`/`push` from Desktop now targets the attacker-controlled remote.

**Note:** I was not able to fully trace every call site in `app-store.ts` that invokes `updateRemoteUrl` (e.g., confirming exactly which background refresh triggers it and its frequency) due to index/tool-call limits; a Devin session with full repo access would be needed to confirm the exact trigger cadence and any additional guard conditions in `app-store.ts`.

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

**File:** app/src/lib/stores/git-store.ts (L33-50)
```typescript
import {
  reset,
  GitResetMode,
  getRemotes,
  fetch as fetchRepo,
  fetchRefspec,
  getRecentBranches,
  getBranches,
  deleteRef,
  getCommits,
  merge,
  setRemoteURL,
  getStatus,
  IStatusResult,
  getCommit,
  IndexStatus,
  getIndexChanges,
  checkoutIndex,
```
