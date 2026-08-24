## Title
Silent, unconfirmed rewrite of the `origin` remote URL from an attacker-controlled GitHub API `clone_url` field - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl()` automatically rewrites the local `origin` remote's URL to whatever `clone_url` value is returned by the GitHub API for the associated repository, with no user confirmation, whenever three guard checks pass. Two of those checks (`urlMatchesRemote` used for "unchanged" detection and the ad-hoc `protocolsMatch` check) use different, narrower notions of "same remote" than what the app elsewhere treats as a trust boundary (host identity). This mirrors the report's root cause: two related checks that are supposed to agree on the same invariant ("is this still the same/trusted remote?") actually diverge, and the gap between them lets a value fully controlled by an external object (here, the GitHub API's `clone_url`) silently pass a security-relevant decision.

### Finding Description
`updateRemoteUrl` is invoked from `repositoryWithRefreshedGitHubRepository` in `app/src/lib/stores/app-store.ts` every time Desktop refreshes GitHub repository metadata from the API (e.g. on account-change refresh, background repository refresh) [1](#0-0) :

```ts
if (repository.gitHubRepository) {
  const gitStore = this.gitStoreCache.get(repository)
  await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
}
```

The update logic is: [2](#0-1) 

The three guard conditions that decide whether the trusted `origin` remote gets rewritten are:
- `protocolsMatch` — computed with Node's `URL.parse()`, comparing only the literal scheme (`https:` vs `https:`), with **no host comparison at all**.
- `remoteUrlUnchanged` — computed with `urlMatchesRemote(gitHubRepository.cloneURL, gitStore.defaultRemote)`, which parses both URLs structurally and only compares `hostname`, `owner`, and `name` [3](#0-2) .
- `!urlsMatch` — same `urlMatchesRemote` function, applied to the *new* API value vs. the current remote.

None of these three checks establishes that the *new* `clone_url` is actually the legitimate host for that repository — they only check that the protocol is unchanged and that the owner/name portion parses out. An attacker who controls the `IAPIRepository` object returned for a repository (a compromised or malicious GitHub Enterprise Server, or any code path that surfaces attacker-influenced API responses) can set `clone_url` to `https://attacker.example/owner/repo.git`. Because:
- `protocolsMatch` only compares scheme, it's trivially `true` (both `https:`).
- `remoteUrlUnchanged` is `true` as long as the user has not hand-edited the `origin` remote away from the previously cached `cloneURL`.
- `urlsMatch` is `false` because the hostname differs, satisfying `!urlsMatch`.

All three guards pass, so `gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)` silently repoints `origin` to the attacker's host [4](#0-3) , calling into `git remote set-url` [5](#0-4) , with no dialog and no user awareness.

Contrast this with the sibling flow for the *upstream* remote, `UpstreamAlreadyExists`, which explicitly requires the user to click "Update" before an upstream remote URL is changed [6](#0-5) . This shows the app's own design intent is that repointing a git remote to a new URL is a security/trust-relevant action requiring consent — an intent that `updateRemoteUrl` contradicts for the primary `origin` remote by doing it unconditionally and silently.

### Impact Explanation
Once `origin` is silently rewritten to an attacker-controlled host, every subsequent `git push`/`fetch`/`pull` for that repository is redirected to attacker infrastructure without the user noticing (the remote name `origin` stays the same in the UI). This satisfies the "silent corruption of what the user commits or pushes" and "git remote/proxy response" categories in the accepted impact list: the user's code (potentially private, proprietary source) is pushed to the attacker's server instead of GitHub, and any credential prompt subsequently shown by Desktop's askpass/credential trampoline would be for the attacker's host, creating a phishing surface for the user's git credentials via the trampoline flow (`app/src/lib/trampoline/trampoline-credential-helper.ts`).

### Likelihood Explanation
The trigger requires only that the attacker control the `clone_url` field of a GitHub API repository object that Desktop fetches for a repo the user already has cloned — realistic for a compromised/malicious GitHub Enterprise Server the user is connected to, or any man-in-the-middle/compromise of the API response path. No local access, no admin rights, and no unnatural user interaction are required; the rewrite happens automatically during routine background repository refreshes.

### Recommendation
Do not silently rewrite `origin` when the API-provided `clone_url` points to a different host than the existing remote. At minimum, require explicit user confirmation (reusing the `UpstreamAlreadyExists`-style dialog) whenever the *hostname* portion of the remote changes, and stop relying on `urlMatchesRemote`'s owner/name-only comparison as a stand-in for "this is still a trusted/expected remote."

### Proof of Concept
1. User has a repository cloned from a (possibly malicious or compromised) GitHub Enterprise Server account, with `origin` set to `https://ghe.example.com/owner/repo.git`.
2. On a routine refresh, `api.fetchRepository(owner, name)` returns an `IAPIRepository` object with `clone_url: "https://attacker.example.com/owner/repo.git"` (attacker controls or has compromised the GHES API response).
3. `repositoryWithRefreshedGitHubRepository` calls `updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)`.
4. `protocolsMatch` = true (`https:` == `https:`), `remoteUrlUnchanged` = true (user hasn't hand-edited origin), `!urlsMatch` = true (hostname differs) ⇒ `gitStore.setRemoteURL('origin', 'https://attacker.example.com/owner/repo.git')` executes silently.
5. The user's next `git push` sends their commits to `attacker.example.com` instead of the real GHES host, with no warning shown.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4904-4907)
```typescript
    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
    }
```

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

**File:** app/src/ui/upstream-already-exists/upstream-already-exists.tsx (L23-47)
```typescript
/**
 * The dialog shown when a repository is a fork but its upstream remote doesn't
 * point to the parent repository.
 */
export class UpstreamAlreadyExists extends React.Component<IUpstreamAlreadyExistsProps> {
  public render() {
    const name = this.props.repository.name
    const gitHubRepository = forceUnwrap(
      'A repository must have a GitHub repository to add an upstream remote',
      this.props.repository.gitHubRepository
    )
    const parent = forceUnwrap(
      'A repository must have a parent repository to add an upstream remote',
      gitHubRepository.parent
    )
    const parentName = parent.fullName
    const existingURL = this.props.existingRemote.url
    const replacementURL = parent.cloneURL
    return (
      <Dialog
        title={
          __DARWIN__ ? 'Upstream Already Exists' : 'Upstream already exists'
        }
        onDismissed={this.props.onDismissed}
        onSubmit={this.onUpdate}
```
