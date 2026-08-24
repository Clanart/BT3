## Title
Automatic, unconfirmed remote URL rewrite from an attacker‑controlled GitHub API field silently redirects future pushes/fetches - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
The original report's broken invariant is: *a single untrusted party can silently rewrite security‑relevant configuration, and no event/confirmation exists for the victim to detect or veto the change.* The Desktop analog is `updateRemoteUrl()`, which automatically calls `git remote set-url` on the repository's default remote whenever the associated GitHub repository's API record (`clone_url`) differs from what's cached — with no user prompt, no diff shown, and only a same-protocol-scheme check as a guard.

### Finding Description
`updateRemoteUrl` is invoked from `repositoryWithRefreshedGitHubRepository`, which is run on essentially every repository refresh path (selecting a repository, background fetching, push, pull, fetch): [1](#0-0) 

The function itself trusts the `clone_url` field returned by `api.fetchRepository(owner, name)` and rewrites the local remote if the protocol *scheme* (not the host) matches and the current URL hasn't been manually diverged from the last cached `cloneURL`: [2](#0-1) 

The only safety checks are:
- `protocolsMatch` — compares `URL.parse(url).protocol` strings only (e.g. `"https:" === "https:"`); it never compares hostnames.
- `remoteUrlUnchanged` — only prevents clobbering a URL the user has *manually* retargeted; it does not validate that the new URL is safe/expected.

Neither check constrains the new host, path, or credentials embedded in the returned `clone_url`. This is called with no confirmation dialog and no log/event emitted, unlike other trust-sensitive actions in the same codebase (e.g. the "Untrusted Server" certificate dialog or the "potentially unsafe" bare/owner-mismatch repository dialogs): [3](#0-2) [4](#0-3) 

The API response object is attacker‑influenced in realistic scenarios: any GitHub Enterprise Server endpoint the user has added is fully attacker-controlled if that server is malicious or compromised, and even on GitHub.com a repository owner/admin can change what `clone_url` is returned for a repo the victim already tracks (transfer/rename or a crafted proxy/MITM response for the API call, since the requirement text explicitly allows "git remote/proxy response" as an attacker vector).

### Impact Explanation
Because this rewrite happens silently on background refresh/fetch/push paths, it results in **silent corruption of what the user pushes and pulls**: the local `origin` (or default) remote is repointed to an attacker-chosen URL with the same scheme, so:
- Subsequent `git push` operations (which use `gitStore.currentRemote`/`defaultRemote` reflecting the rewritten config) send the user's code to the attacker's endpoint instead of GitHub.
- Subsequent `git fetch`/pull operations bring in attacker-controlled history/objects into the user's local repository without any warning, which can subsequently be merged or checked out.
- Because this happens through the normal push/fetch code path, the trampoline credential helper may present it as a legitimate destination, risking credential exposure to the new host in edge cases where endpoint classification treats it as generic and prompts for/attaches stored generic credentials.

This satisfies the "silent corruption of what the user commits or pushes" and "git remote/proxy response" categories from the valid-impact list, entirely from unprivileged, no-local-access conditions.

### Likelihood Explanation
The rewrite path is exercised automatically and frequently — `repositoryWithRefreshedGitHubRepository` runs on repository selection, background fetch, and around every push/pull/fetch operation: [5](#0-4) 

No user interaction beyond having previously added the (attacker-influenced) endpoint/repository is required, and the only defensive check (`protocolsMatch`) is a scheme-only comparison that does not defend against a host swap. This makes the likelihood moderate-to-high for any user tracking a repository on an untrusted/compromised GHES endpoint or where the API response can otherwise be manipulated in transit.

### Recommendation
- Validate the *hostname* (not just protocol scheme) of the incoming `clone_url` against the endpoint the account is authenticated against before silently rewriting the remote.
- Require explicit user confirmation (similar to the existing "Untrusted Server"/"unsafe repository" dialogs) before changing a tracked remote's URL to a different host.
- Emit a log entry/event whenever `setRemoteURL` is invoked automatically so the change is auditable, mirroring the "reconstruct engine state from events" recommendation in the original report.

### Proof of Concept
1. Add a GitHub Enterprise Server (or a proxied/MITM'd `github.com` API response) account in Desktop and clone/track a repository from it, so `gitHubRepository.cloneURL` and `origin` both equal `https://ghe.example.com/acme/widgets`.
2. From the (malicious or compromised) server, change the JSON returned for `GET /repos/acme/widgets` so that `clone_url` becomes `https://ghe.example.com.attacker.tld/acme/widgets` (same `https:` scheme, different host).
3. Trigger any refresh path (select the repository, background fetch, or a manual fetch/push) so `repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl` runs.
4. Observe that `protocolsMatch` passes (scheme is identical) and `remoteUrlUnchanged` is true, so `gitStore.setRemoteURL('origin', 'https://ghe.example.com.attacker.tld/acme/widgets')` executes with no dialog and no log entry.
5. The next `git push`/`git fetch` silently targets the attacker's host.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4886-4907)
```typescript

    const { account, owner, name } = match
    const { endpoint } = account
    const api = API.fromAccount(account)
    const apiRepo = await api.fetchRepository(owner, name)

    if (apiRepo === null) {
      // If the request fails, we want to preserve the existing GitHub
      // repository info. But if we didn't have a GitHub repository already or
      // the endpoint changed, the skeleton repository is better than nothing.
      if (endpoint !== repository.gitHubRepository?.endpoint) {
        const ghRepo = await repoStore.upsertGitHubRepositoryFromMatch(match)
        return repoStore.setGitHubRepository(repository, ghRepo)
      }

      return repository
    }

    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
    }
```

**File:** app/src/lib/stores/app-store.ts (L5895-5915)
```typescript
  public _fetch(repository: Repository, fetchType: FetchType): Promise<void> {
    return this.withRefreshedGitHubRepository(repository, repository => {
      return this.performFetch(repository, fetchType)
    })
  }

  /**
   * Fetch a particular remote in a repository.
   *
   * Note that this method will not perform the fetch of the specified remote
   * if _any_ fetches or pulls are currently in-progress.
   */
  private _fetchRemote(
    repository: Repository,
    remote: IRemote,
    fetchType: FetchType
  ): Promise<void> {
    return this.withRefreshedGitHubRepository(repository, repository => {
      return this.performFetch(repository, fetchType, [remote])
    })
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

**File:** app/src/ui/untrusted-certificate/untrusted-certificate.tsx (L35-52)
```typescript
  public render() {
    const host = URL.parse(this.props.url).hostname

    return (
      <Dialog
        title={__DARWIN__ ? 'Untrusted Server' : 'Untrusted server'}
        onDismissed={this.props.onDismissed}
        onSubmit={this.onContinue}
        type={__DARWIN__ ? 'warning' : 'error'}
      >
        <DialogContent>
          <p>
            GitHub Desktop cannot verify the identity of {host}. The certificate
            ({this.props.certificate.subjectName}) is invalid or untrusted.{' '}
            <strong>
              This may indicate attackers are trying to steal your data.
            </strong>
          </p>
```

**File:** app/src/ui/missing-repository.tsx (L111-134)
```typescript
    if (isPathUnsafe) {
      return (
        <UiView id="missing-repository-view">
          <div className="title-container">
            <div className="title">
              {this.props.repository.name} is potentially unsafe
            </div>
            <div className="details">
              <p>
                The Git repository at <Ref>{unsafePath}</Ref> appears to be
                owned by another user on your machine. Adding untrusted
                repositories may automatically execute files in the repository.
              </p>
              <p>
                If you trust the owner of the directory you can add an exception
                for this directory in order to continue.
              </p>
            </div>
          </div>

          <Row>{buttons}</Row>
        </UiView>
      )
    }
```
