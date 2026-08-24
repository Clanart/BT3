### Title
Automatic silent remote-URL rewrite based on unverified GitHub API repository data enables repo-hijacking / push redirection - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl` silently rewrites a repository's `origin` remote URL to whatever `clone_url` the GitHub API returns for the associated `GitHubRepository`, with no user confirmation, as long as a loose set of heuristic conditions are met. The function trusts the API-supplied `clone_url` field as the new "correct" location for the repository, which means any GitHub API response that claims to be the "current" location of a previously-known repo (e.g., after a rename, transfer, or repository-name squatting/hijack) can cause Desktop to silently redirect the user's local git remote to an attacker-controlled host/path. [1](#0-0) 

### Finding Description
`updateRemoteUrl` compares the current default remote URL against the previously stored `gitHubRepository.cloneURL` and the freshly fetched `apiRepo.clone_url`: [2](#0-1) 

The only checks performed before calling `gitStore.setRemoteURL` are:
- The URL scheme (http/https/ssh) hasn't changed (`protocolsMatch`), not the hostname.
- The current remote still textually matches the previously cached `cloneURL` (`remoteUrlUnchanged`), i.e., the user hasn't manually edited the remote.
- The new URL doesn't already match the current remote (`!urlsMatch`).

Crucially, there is no verification that the "new" repository at `apiRepo.clone_url` is actually the *same* repository entity (e.g., by GitHub's stable numeric repository `id`) as the one the user originally added. `urlMatchesRemote`/`urlsMatch` only do string/host/owner/name comparisons on the URL itself: [3](#0-2) 

This is the exact same class of bug as the report: the wrong principal is trusted to redirect a critical target. In the report, the fee recipient (an untrusted party w.r.t. operator identity) was allowed to redirect the operator address. Here, an arbitrary GitHub API repository object — which is attacker-influenceable in real-world scenarios such as name-squatting after a rename/transfer, a compromised/malicious GHES instance, or an org takeover — is trusted to redirect the user's trusted git remote, with no re-authentication of "is this really the same repository I intended to work with."

Because the check is purely URL/owner/name-based (not an immutable repository ID check), an attacker who creates a new repository under the freed `owner/name` combination that a victim's stale `GitHubRepository` record still refers to can cause the victim's next repository refresh to silently repoint their `origin` remote at the attacker's repository.

### Impact Explanation
Once the remote URL is silently rewritten:
- Future `git push` operations from the victim's Desktop will send commits/branches to the attacker-controlled remote, exposing private code and enabling credential/token exposure over the connection Desktop establishes to that host.
- Future `git fetch`/`pull` operations will pull attacker-controlled content into the local repository, which can corrupt what the user believes they are committing/merging, or introduce malicious hooks/build artifacts.
- The user receives no explicit warning/confirmation dialog (unlike the `UpstreamAlreadyExists` dialog which does prompt for the *upstream* remote) — [4](#0-3)  — this asymmetry shows the app developers do consider unprompted remote redirection risky enough to require confirmation in one code path, but `updateRemoteUrl` performs the equivalent action on the primary/default remote without any prompt.

### Likelihood Explanation
This requires an attacker to control a GitHub API object (a new repository claiming the identity of the one the user's app-managed `GitHubRepository` record previously pointed to) — squatting a renamed/deleted repo's `owner/name`, or standing up a malicious GHES endpoint the victim's Desktop instance is configured against. This matches the allowed attacker model ("attacker controls...a GitHub API object...or a git remote/proxy response"). No local access, admin rights, or social engineering beyond normal Desktop usage (refreshing the repository, which happens automatically/periodically) is required.

### Recommendation
- Do not automatically rewrite the default/origin remote based solely on URL/name matching against the GitHub API response. Instead, verify the immutable numeric repository `id` remains identical between the previously stored `GitHubRepository` and the freshly fetched `apiRepo` before treating it as "the same repository that moved."
- If the ID differs (or cannot be reconciled), treat this as a new/possibly-hijacked repository and require explicit user confirmation, mirroring the existing `UpstreamAlreadyExists` dialog pattern, rather than silently calling `gitStore.setRemoteURL`.

### Proof of Concept
1. Victim adds/clones `https://github.com/alice/project` in GitHub Desktop; Desktop stores a `GitHubRepository` record with `cloneURL = https://github.com/alice/project`.
2. Alice renames/transfers/deletes the repository, freeing the `alice/project` name (or the attacker otherwise controls a GitHub Enterprise endpoint returning attacker data for that identifier).
3. Attacker creates a new repository at the same `owner/name` (`alice/project`) that the victim's stale record still references, or provides a malicious API response for that endpoint.
4. On the victim's next repository refresh, Desktop calls the API for `alice/project`, receiving the attacker's repository object with `clone_url = https://github.com/alice/project` (attacker's new repo, unrelated to the original by ID).
5. `updateRemoteUrl` sees `protocolsMatch = true`, `remoteUrlUnchanged = true` (victim never manually edited origin), and `urlsMatch` may be false only if the attacker changes the case/format slightly, or true if names collide exactly — either way there is no ID check preventing the swap when the API record is treated as authoritative — and calls `gitStore.setRemoteURL('origin', attackerCloneUrl)`, silently repointing the victim's remote.
6. The victim's next `git push`/`fetch` interacts with the attacker's repository without any warning dialog. [5](#0-4)

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

**File:** app/src/ui/upstream-already-exists/upstream-already-exists.tsx (L27-76)
```typescript
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
        type="warning"
      >
        <DialogContent>
          <p>
            The repository <Ref>{name}</Ref> is a fork of{' '}
            <Ref>{parentName}</Ref>, but its <Ref>{UpstreamRemoteName}</Ref>{' '}
            remote points elsewhere.
          </p>
          <ul>
            <li>
              Current: <Ref>{existingURL}</Ref>
            </li>
            <li>
              Expected: <Ref>{replacementURL}</Ref>
            </li>
          </ul>
          <p>Would you like to update the remote to use the expected URL?</p>
        </DialogContent>
        <DialogFooter>
          <OkCancelButtonGroup
            destructive={true}
            okButtonText="Update"
            cancelButtonText="Ignore"
            onCancelButtonClick={this.onIgnore}
          />
        </DialogFooter>
      </Dialog>
    )
  }
```
