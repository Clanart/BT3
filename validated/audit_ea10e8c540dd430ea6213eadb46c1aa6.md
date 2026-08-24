[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** app/src/lib/stores/sign-in-store.ts (L284-303)
```typescript
    const csrfToken = crypto.randomUUID()

    new Promise<Account>((resolve, reject) => {
      const { endpoint, resultCallback } = currentState
      log.info('[SignInStore] initializing OAuth flow')
      this.setState({
        kind: SignInStep.Authentication,
        endpoint,
        resultCallback,
        error: null,
        loading: true,
        oauthState: {
          state: csrfToken,
          endpoint,
          onAuthCompleted: resolve,
          onAuthError: reject,
        },
      })
      shell.openExternal(getOAuthAuthorizationURL(endpoint, csrfToken))
    })
```

**File:** app/src/lib/stores/sign-in-store.ts (L332-346)
```typescript
  public async resolveOAuthRequest(action: IOAuthAction) {
    if (!this.state || this.state.kind !== SignInStep.Authentication) {
      return
    }

    if (!this.state.oauthState) {
      return
    }

    if (this.state.oauthState.state !== action.state) {
      log.warn(
        'requestAuthenticatedUser was not called with valid OAuth state. This is likely due to a browser reloading the callback URL. Contact GitHub Support if you believe this is an error'
      )
      return
    }
```

**File:** app/src/lib/remote-parsing.ts (L72-88)
```typescript
/**
 * Extracts a safe single-component directory name from a URL-derived repo name.
 *
 * Mirrors the approach of git's `git_url_basename()` in `dir.c`: treat `/`,
 * `\`, and `:` as path separators, take the last non-empty component, strip a
 * trailing `.git` suffix, and reject traversal segments. This ensures the
 * result is always a single path component that cannot escape the parent
 * directory when passed to `Path.join()`.
 *
 * Examples:
 *  - `"Hello-World"` → `"Hello-World"` (unchanged)
 *  - `"desktop.git/../../otherdir"` → `"otherdir"` (last component, traversal segments skipped)
 *  - `".."` → `null` (traversal-only name rejected)
 *
 * See: https://github.com/git/git/blob/master/dir.c (`git_url_basename`)
 */
export function sanitizeCloneName(name: string): string | null {
```

**File:** app/test/unit/clone-path-safety-test.ts (L43-56)
```typescript
  it('traversal payload clone path stays contained (POSIX)', () => {
    const result = parseRepositoryIdentifier(
      'https://evil.com/owner/x..\\..\\..\\.\\.ssh.git'
    )
    assert(result !== null)
    const safeName = sanitizeCloneName(result.name)
    assert(safeName !== null)
    const baseDir = '/Users/victim/Documents/GitHub'
    const resolved = Path.resolve(Path.join(baseDir, safeName))
    assert(
      resolved.startsWith(Path.resolve(baseDir)),
      `Clone path "${resolved}" escapes base dir`
    )
  })
```

**File:** app/src/lib/stores/updates/update-remote-url.ts (L1-44)
```typescript
import { IAPIRepository } from '../../api'
import { GitStore } from '../git-store'
import { urlMatchesRemote } from '../../repository-matching'
import * as URL from 'url'
import { GitHubRepository } from '../../../models/github-repository'

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

**File:** app/test/unit/stores/updates/update-remote-url-test.ts (L96-128)
```typescript
  it("doesn't update repository's remote url if protocols don't match", async t => {
    const originalUrl = 'git@github.com:desktop/desktop.git'
    const sshApiRepository = {
      ...apiRepository,
      clone_url: originalUrl,
    }
    const { gitHubRepository, gitStore } = await createRepository(
      t,
      sshApiRepository
    )
    const updatedUrl = 'https://github.com/my-user/my-updated-repo'
    const updatedApiRepository = { ...apiRepository, clone_url: updatedUrl }

    await updateRemoteUrl(gitStore, gitHubRepository, updatedApiRepository)
    assert(gitStore.currentRemote !== null)
    assert.equal(gitStore.currentRemote.url, originalUrl)
  })

  it("doesn't update the repository's remote url if it differs from the default from the github API", async t => {
    const originalUrl = 'https://github.com/my-user/something-different'
    const { gitHubRepository, gitStore } = await createRepository(
      t,
      apiRepository,
      originalUrl
    )

    const updatedUrl = 'https://github.com/my-user/my-updated-repo'
    const updatedApiRepository = { ...apiRepository, clone_url: updatedUrl }

    await updateRemoteUrl(gitStore, gitHubRepository, updatedApiRepository)
    assert(gitStore.currentRemote !== null)
    assert.equal(gitStore.currentRemote.url, originalUrl)
  })
```

**File:** app/src/lib/git/interpret-trailers.ts (L152-176)
```typescript
export async function mergeTrailers(
  repository: Repository,
  commitMessage: string,
  trailers: ReadonlyArray<ITrailer>,
  unfold: boolean = false
) {
  const args = ['interpret-trailers']

  // See https://github.com/git/git/blob/ebf3c04b262aa/Documentation/git-interpret-trailers.txt#L129-L132
  args.push('--no-divider')

  if (unfold) {
    args.push('--unfold')
  }

  for (const trailer of trailers) {
    args.push('--trailer', `${trailer.token}=${trailer.value}`)
  }

  const result = await git(args, repository.path, 'mergeTrailers', {
    stdin: commitMessage,
  })

  return result.stdout
}
```
