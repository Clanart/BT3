### Title
Auto-rewrite of trusted git remote from unvalidated API `clone_url` allows silent push/fetch redirection - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl()` will automatically call `gitStore.setRemoteURL()` to overwrite a repository's `origin` remote whenever the GitHub API's `clone_url` for the associated `GitHubRepository` differs from the current remote, as long as the URL protocol matches and the remote hasn't been "manually" changed away from the previously cached `cloneURL`. [1](#0-0)  The value being trusted (`apiRepo.clone_url`) is the same kind of externally-supplied datum in the report's bid-token analogy: one "authoritative" source (the account's configured `endpoint`, chosen by the user when they added the account) and one attacker-reachable source (the response body of an API call made to that endpoint) are never cross-checked for hostname consistency before being used to mutate the trusted local git configuration.

### Finding Description
`repositoryWithRefreshedGitHubRepository()` fetches `apiRepo` from `API.fromAccount(account).fetchRepository(owner, name)`, using the account's own `endpoint`, and then calls `updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)`. [2](#0-1)  Inside `updateRemoteUrl`, the only validations performed are: (1) that the URL *protocol* (ssh/https) of the old and new remote match, and (2) that the previously cached `gitHubRepository.cloneURL` still matches the currently configured remote (i.e., the user hasn't manually repointed it). [3](#0-2)  There is no check that the *hostname* of the new `clone_url` returned by the API matches the hostname of the account's `endpoint` that was used to authenticate the request. `urlMatchesRemote`/`parseRemote` only compare the old vs. new remote to each other, not against the trusted endpoint. [4](#0-3) 

This mirrors exactly the report's broken invariant: a value is defined in two places (the DAppConfig-equivalent "account endpoint the user trusts" vs. the SolverOperation-equivalent "clone_url returned in an API response") and nothing on the enforcement path (`verifySolverOp()`-equivalent `updateRemoteUrl()`) checks that they agree before the value is used to drive further critical actions (bid settlement / here, git push-fetch destination).

### Impact Explanation
If a GitHub Enterprise server is compromised, on-path (MITM against a self-hosted/at-risk GHE instance), or simply misconfigured/malicious, it can return an API repository object whose `clone_url` points to a different host than the endpoint the user configured and trusts. Desktop will silently rewrite the local `origin` remote to that attacker-controlled URL via `git remote set-url`, with no user prompt or confirmation, changing where the next `git push`/`git fetch` goes. Because the trampoline credential helper resolves accounts primarily by matching the *remote URL's* origin to an account's endpoint [5](#0-4) , if the rewritten hostname coincidentally matches another configured account (or triggers the generic/plain credential-prompt flow), credentials or push traffic could be silently redirected to the wrong destination — corrupting where the user's future commits are pushed, or exposing generic credentials to a different host, without any visible warning (in contrast to the explicit warning dialog shown for the analogous "upstream remote mismatch" case). [6](#0-5) 

### Likelihood Explanation
Likelihood is moderate-to-low in practice for GitHub.com (where `clone_url` shape is highly constrained), but realistic for GitHub Enterprise Server deployments, which Desktop explicitly supports, where the "trusted" host is whatever the user configures via `enterprise-validate-url.ts` and the returned API payload is fully under the control of that server. [7](#0-6)  This code path (`repositoryWithRefreshedGitHubRepository`) runs automatically during routine background repository refreshes, not gated behind any unusual user action, satisfying the "unprivileged, attacker controls a GitHub API object" impact criterion.

### Recommendation
In `updateRemoteUrl()`, before calling `gitStore.setRemoteURL()`, parse the hostname out of `apiRepo.clone_url` and require it to match the hostname derived from `getHTMLURL(gitHubRepository.endpoint)` (the endpoint used to fetch the data). Reject/skip the auto-update (and optionally surface a warning to the user, similar to `UpstreamAlreadyExists`) if the hostnames diverge, rather than silently trusting whatever host the API response names.

### Proof of Concept
Not independently executable from static analysis alone (would require standing up or MITM'ing a GHE endpoint to return a crafted `clone_url`), but the code path is deterministic:
1. User adds a GitHub Enterprise account with endpoint `https://ghe.corp.example`.
2. Desktop calls `api.fetchRepository(owner, name)` against that endpoint during a background refresh (`repositoryWithRefreshedGitHubRepository`). [2](#0-1) 
3. The (compromised/malicious) server responds with `clone_url: "https://attacker.example/owner/name.git"` (same protocol, so `protocolsMatch` is true) while the previously-cached `cloneURL` still equals the current remote (`remoteUrlUnchanged` is true).
4. `updateRemoteUrl()` calls `gitStore.setRemoteURL('origin', 'https://attacker.example/owner/name.git')` [8](#0-7) , silently redirecting all future pushes/fetches without any user confirmation. [9](#0-8)

### Citations

**File:** app/src/lib/stores/updates/update-remote-url.ts (L1-45)
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
}
```

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

**File:** app/src/lib/trampoline/find-account.ts (L20-29)
```typescript
export async function findGitHubTrampolineAccount(
  accountsStore: AccountsStore,
  remoteUrl: string
): Promise<Account | undefined> {
  const accounts = await accountsStore.getAll()
  const parsedUrl = new URL(remoteUrl)
  return accounts.find(
    a => new URL(getHTMLURL(a.endpoint)).origin === parsedUrl.origin
  )
}
```

**File:** app/src/ui/upstream-already-exists/upstream-already-exists.tsx (L23-41)
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
```

**File:** app/src/ui/lib/enterprise-validate-url.ts (L14-45)
```typescript
export function validateURL(address: string): string {
  // ensure user has specified text and not just whitespace
  // we will interact with this server so we can be fairly
  // relaxed here about what we accept for the server name
  const trimmed = address.trim()
  if (trimmed.length === 0) {
    const error = new Error('Unknown address')
    error.name = InvalidURLErrorName
    throw error
  }

  let url = URL.parse(trimmed)
  if (!url.host) {
    // E.g., if they user entered 'ghe.io', let's assume they're using https.
    address = `https://${trimmed}`
    url = URL.parse(address)
  }

  if (!url.protocol) {
    const error = new Error('Invalid URL')
    error.name = InvalidURLErrorName
    throw error
  }

  if (url.protocol !== 'https:') {
    const error = new Error('Invalid protocol')
    error.name = InvalidProtocolErrorName
    throw error
  }

  return address
}
```
