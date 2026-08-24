### Title
Automatic, unconfirmed rewrite of a repository's git remote URL from an untrusted GitHub API response - (File: app/src/lib/stores/updates/update-remote-url.ts)

### Summary
`updateRemoteUrl()` silently runs `git remote set-url` against a repository's `origin` remote whenever a background/foreground refresh of the associated GitHub repository sees a different `clone_url` than what Desktop had previously cached, with no user prompt, confirmation dialog, or diff shown. The only guards are protocol-matching and a "was the remote unchanged since last known state" heuristic — neither of which validates that the new `clone_url` value legitimately belongs to the same GitHub repository/owner the user intended to work with. This mirrors the `CumulativeMerkleDrop` bug class: a value (there, `merkleRoot`; here, the git remote URL) is overwritten based on externally supplied input without a strict equality/no-op or provenance check, so an attacker-influenced input silently mutates state the user relies on.

### Finding Description
`updateRemoteUrl` is invoked from `AppStore.repositoryWithRefreshedGitHubRepository` (`app/src/lib/stores/app-store.ts:4904-4907`) any time Desktop refreshes the GitHub metadata for a tracked repository via `api.fetchRepository(owner, name)`. The comparison logic is: [1](#0-0) 

- `remoteUrl` = the git remote's current URL.
- `updatedRemoteUrl` = `apiRepo.clone_url`, taken directly from the GitHub API response object (`fetchRepository`), which is attacker-influenced input per the accepted threat model ("attacker controls ... a GitHub API object").
- `remoteUrlUnchanged` is computed by comparing the **locally cached** `gitHubRepository.cloneURL` (from the last time Desktop stored GitHub metadata) against the **current** git remote — not against any cryptographic or otherwise trustworthy anchor.
- If `protocolsMatch && remoteUrlUnchanged && !urlsMatch`, Desktop calls `gitStore.setRemoteURL(...)` → `setRemoteURL()` in `app/src/lib/git/remote.ts:57-64`, which runs `git remote set-url <name> <url>` unconditionally.

There is no re-confirmation that the "new" `clone_url` still points to the same owner/repo pair the user is collaborating with, and no user-visible prompt is shown before the remote is rewritten — the change happens as a side effect of a routine background metadata refresh (`repositoryWithRefreshedGitHubRepository`, called throughout `app-store.ts`). This is analogous to the audited contracts allowing `merkleRoot_` to be swapped in without a check that the new value is meaningfully different/validated — here, the remote URL is swapped in based on server-returned data without validating it corresponds to the trusted origin the user expects.

### Impact Explanation
If a user's GitHub API responses can be influenced (e.g., a compromised/malicious GitHub Enterprise Server the account is configured against, a repository transfer/rename race, or a MITM/compromised proxy sitting in front of the API endpoint), Desktop will silently repoint the user's `origin` remote to a different host/repository without any confirmation dialog. Subsequent `git push` operations initiated by the user through Desktop's UI would then send code to the attacker-controlled remote, satisfying the "silent corruption of what the user commits or pushes" impact criterion. Because the URL rewrite happens transparently during a routine background refresh, the user has no visual cue in the normal workflow that their push destination changed.

### Likelihood Explanation
The precondition (`remoteUrlUnchanged`) requires that the remote had not already diverged from Desktop's last-known GitHub `cloneURL`, which is generally true for most users who never manually edit their remote. The refresh path (`repositoryWithRefreshedGitHubRepository`) runs as part of normal repository-refresh flows, so no unusual user action beyond opening/using the app is required. The main constraint is the attacker's ability to influence the `IAPIFullRepository.clone_url` field returned to the victim's Desktop client, which requires control of a GHES/API endpoint or a MITM position on API traffic — a real threat surface but not trivially exploitable against `api.github.com` itself given TLS.

### Recommendation
Add a strict validation/no-op guard analogous to the recommended Solidity fix, e.g.:
- Do not accept `apiRepo.clone_url` as authoritative for silently rewriting the remote unless the returned repository's stable identifier (owner/repo ID, not just URL string) matches the one already associated with the tracked `GitHubRepository`.
- Before calling `gitStore.setRemoteURL`, require explicit user confirmation (a dialog) when the effective owner/repo differs from what was previously known, rather than performing the rewrite unconditionally in the background.
- Treat `updatedRemoteUrl === remoteUrl` (or equivalent normalized equality) as a true no-op and short-circuit, and treat any actual change as security-sensitive, gated behind user consent.

### Proof of Concept
1. User adds an account pointed at a GitHub Enterprise Server (or any endpoint an attacker can influence/MITM) and clones a repository through Desktop, so `gitHubRepository.cloneURL` and the `origin` remote both point to `https://ghe.example.com/org/repo`.
2. Attacker, controlling the GHES API responses (or intercepting them), makes a subsequent `fetchRepository(owner, name)` response return `clone_url: "https://ghe.example.com/org/repo-evil"` (or an entirely different host) while keeping the same protocol.
3. During Desktop's normal background refresh, `AppStore.repositoryWithRefreshedGitHubRepository` calls `updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)` — `app/src/lib/stores/app-store.ts:4904-4907`.
4. Since `protocolsMatch` is true and `remoteUrlUnchanged` is true (remote still matches the last cached `cloneURL`), and `urlsMatch` is false (new URL differs), the code executes `gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)` — `app/src/lib/stores/updates/update-remote-url.ts:42-44` — silently repointing `origin` to `repo-evil` with no user prompt.
5. The user later pushes via Desktop's UI, and the push target is the attacker-controlled `repo-evil`, without the user having consciously changed anything. [2](#0-1)

### Citations

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
