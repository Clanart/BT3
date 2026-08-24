Based on the evidence gathered, I found a credible analog: GitHub Desktop silently rewrites a repository's local git remote URL based on data returned from the GitHub API, without any user confirmation, and the validation used to decide whether it's "safe" to do so is comparison of URL components — not any cryptographic/identity binding — which an attacker who controls the upstream repository object can manipulate.

### Title
Silent, unconfirmed remote URL rewrite driven by attacker-controlled GitHub API `clone_url` - (File: app/src/lib/stores/updates/update-remote-url.ts)

### Summary
`updateRemoteUrl()` [1](#0-0)  automatically calls `gitStore.setRemoteURL()` to overwrite the user's local `origin` remote whenever the GitHub API's `clone_url` for the associated `GitHubRepository` no longer matches the local remote, as long as a same-protocol/heuristic check passes [2](#0-1) . This mirrors the report's core issue class — a critical "ownership"/pointer value (here, the push/fetch destination of the repo) is mutated based on an unchecked externally-supplied value, with no confirmation step, analogous to how `transferGovernor()` mutated `governor` from an unchecked parameter.

### Finding Description
The function is invoked with `apiRepo` — data fetched from the GitHub API for the repository the user has cloned [3](#0-2) . The only checks performed before rewriting the remote are:
1. `protocolsMatch` — both old and new URLs parse to the same URL scheme [4](#0-3) .
2. `remoteUrlUnchanged` — the existing remote still structurally matches the previously known GitHub `cloneURL` via `urlMatchesRemote()`, which itself only compares `hostname`, `owner`, and `name` case-insensitively after regex-parsing the URLs [5](#0-4) .
3. `!urlsMatch` — the new API URL differs from the current remote.

If all three hold, the code silently calls `gitStore.setRemoteURL(...)` [6](#0-5) , which executes `git remote set-url` [7](#0-6)  with **no user prompt, confirmation dialog, or diff shown**. Since the value being trusted (`apiRepo.clone_url`) originates from a GitHub API repository object, and the repository owner (an unprivileged actor from Desktop's threat-boundary perspective) fully controls what `clone_url` their own repository record reports (e.g., by renaming/moving the repo, or having it recreated under different metadata), this is directly analogous to an "unchecked externally supplied address" overwriting a security-relevant pointer — here, the destination the local client will fetch from and push to.

### Impact Explanation
A user who has cloned a repository owned by an untrusted or later-compromised party can have their local `origin` remote silently repointed to a URL the repo owner chooses, without any indication in the UI beyond a log line. Because subsequent `git fetch`/`git pull` operations use this remote, an attacker who controls the upstream repository can redirect a victim's fetches to a different (attacker-controlled) repository, allowing history/commit injection into the victim's working copy under the guise of "the same project," or redirect where the user believes they are pushing/collaborating. This matches the "silent corruption of what the user commits or pushes" impact category, driven purely by data returned from a GitHub API object the attacker influences.

### Likelihood Explanation
The check relies on structural URL matching (`urlMatchesRemote`/`parseRemote`) rather than any stable per-repository identity (e.g., API-provided permanent ID), and the update path executes with no user confirmation [6](#0-5) . This makes the path reachable during normal background repository-metadata refreshes, without requiring any unusual user action, whenever a tracked repository's `clone_url` changes on the API side (rename/transfer/re-association). I could not fully trace the exact caller conditions in `app-store.ts` (which invokes `updateRemoteUrl`) within the available context, so the precise refresh trigger and any additional gating logic there remain unverified.

### Recommendation
Do not silently rewrite the local remote URL based solely on API-reported `clone_url` matching heuristics. At minimum, bind the update to a stable, attacker-non-controllable identifier (e.g., the GitHub repository's immutable numeric ID) already used elsewhere for `GitHubRepository` records, and require explicit user confirmation before changing `origin`'s URL, mirroring the recommendation of adding an explicit validation gate before mutating a security-relevant pointer (as recommended for `transferGovernor`/`initialize` in the original report).

### Proof of Concept
1. Victim clones a public GitHub repository owned by Attacker; Desktop associates the local repo with the `GitHubRepository` record for that API object.
2. Attacker renames/moves their repository (or otherwise causes the API's `clone_url` for that same repository ID to change) to point at a different, attacker-controlled remote sharing a scheme (e.g., still `https://github.com/attacker/other-repo.git`), so `protocolsMatch` stays true.
3. On Desktop's next background metadata refresh, `updateRemoteUrl` computes `remoteUrlUnchanged = true` (victim never manually edited the remote) and `urlsMatch = false`, so it calls `gitStore.setRemoteURL(defaultRemote.name, updatedRemoteUrl)` [6](#0-5) , silently repointing `origin` to the attacker-chosen URL with no dialog.
4. Victim's next `git fetch`/`pull` in Desktop now pulls from the attacker-chosen destination, believing it is the same project.

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

**File:** app/src/lib/stores/updates/update-remote-url.ts (L22-44)
```typescript
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
