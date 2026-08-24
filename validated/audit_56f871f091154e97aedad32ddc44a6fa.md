### Title
Silent, automatic rewrite of a user-configured git remote URL based on unauthenticated trust in the GitHub API's `clone_url` field - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
GitHub Desktop periodically refreshes cached `GitHubRepository` metadata and, as a side effect, silently calls `git remote set-url` on the user's `origin` remote whenever the API-reported `clone_url` differs from what's on disk, without any user confirmation. This mirrors the report's underlying bug class: a value the user established explicitly (here, the git remote URL; in the original report, `time_unit`) is mutated automatically by administrative/external input, and downstream operations (`git push`/`git fetch`, credential lookups) implicitly trust the mutated value without re-validating the invariant the user relied on.

### Finding Description
`updateRemoteUrl` in [1](#0-0)  compares the local `origin` remote URL against `apiRepo.clone_url` returned from a GitHub API call, and if the protocol matches and the remote hasn't been manually changed away from the previously cached `cloneURL`, it calls `gitStore.setRemoteURL(...)` to rewrite the remote — with no user prompt.

This is invoked from `repositoryWithRefreshedGitHubRepository`, which runs as part of routine background repository refresh logic (e.g., after fetches/pushes) and fetches the repository object live from the API via `api.fetchRepository(owner, name)`: [2](#0-1) .

The actual rewrite happens in `GitStore.setRemoteURL`, which shells out to `git remote set-url`: [3](#0-2)  and [4](#0-3) .

The guard logic only checks:
1. Protocol equality (both `https:` or neither has a parseable protocol, i.e., SSH `git@host:owner/repo` form is treated as "protocols match" since `URL.parse` fails silently for it and both being `null` is not actually excluded — see `protocolsMatch` computed from `URL.parse`),
2. That the *current* remote still matches the *previously cached* `GitHubRepository.cloneURL` (`remoteUrlUnchanged`),
3. That the new API value differs from the current remote (`!urlsMatch`).

Nothing in this guard validates that the **host** in the new `clone_url` is the same host the repository was originally added from, nor does it require any interactive confirmation. The invariant "the remote the user is pushing/fetching from is one the user explicitly configured or that was fetched from the account's own trusted endpoint" is broken the moment the API response for `fetchRepository` is attacker-influenced (e.g., a compromised/malicious GitHub Enterprise Server the account is configured against, or a network-level actor able to tamper with API responses to that endpoint, or a legitimately-permitted account/rename action that redirects `clone_url` to an owner outside the user's control). Because `findAccountForRemoteURL`/API calls are made using the account tied to `repository.gitHubRepository.endpoint` ( [5](#0-4) ), any entity that can influence what that endpoint's API returns for `fetchRepository` can redirect where the user's next `git push`/`git fetch` goes.

### Impact Explanation
If the remote URL is silently rewritten to point at an attacker-controlled host (same protocol, different host/owner), the next time the user pushes, their commits (potentially including proprietary source, secrets committed accidentally, etc.) are sent to the attacker's server instead of the intended GitHub repository — a silent corruption of what the user pushes, with no visible warning in the UI (the Repository Settings "Remote" dialog would show the new value only if the user manually opens it, per [6](#0-5) ). Because the credential/trampoline layer resolves credentials by matching the target remote's host against known accounts ( [7](#0-6) ), pushing/fetching to an unexpected but same-protocol host under attacker control can also affect what credentials/tokens are transmitted for that operation, depending on the credential helper resolution path.

### Likelihood Explanation
Exploitation requires an attacker who can affect the API response body for `fetchRepository(owner, name)` on the endpoint the user's account is tied to (e.g., a rogue/compromised GitHub Enterprise Server, or a network position able to tamper with that endpoint's HTTPS responses despite TLS — this needs to be a scenario where the "trusted" API endpoint itself is not fully trustworthy, which is plausible for GHE instances added by IT that later get compromised, or where `clone_url` changes due to a rename by any actor with sufficient repo permissions redirecting ownership). This is not a purely local/physical-access scenario and doesn't require prior malware on the host; it fits the "git remote/proxy response" and "GitHub API object" attacker-controlled categories called out as valid impact classes. However, it does require some level of control over an API response the app treats as authoritative, which is a meaningfully privileged position compared to a plain malicious repository clone — the report's core weakness (no reconfirmation when a trusted value changes) is present and unmitigated, but real-world exploitability is bounded by needing to control that API surface.

### Recommendation
- Require explicit user confirmation before silently rewriting an existing configured remote URL, especially when the host component changes.
- Extend `updateRemoteUrl`'s guard to require the new `clone_url` hostname matches the existing remote's hostname (or the account's known endpoint host) before auto-updating, and treat host changes as requiring confirmation rather than silent mutation.
- Log/notify the user whenever the git remote is changed automatically as a result of an API refresh, mirroring the "don't silently change a value users rely on" remediation used for `time_unit` in the reference report (i.e., disallow automatic modification of a security-relevant, user-established value without explicit reconfirmation).

### Proof of Concept
1. User adds a repository in Desktop that is associated with a GitHub Enterprise Server endpoint the user's org controls (`repository.gitHubRepository.endpoint`).
2. That endpoint (or a network path to it) is compromised/tampered such that `GET /repos/{owner}/{name}` returns a `clone_url` pointing to `https://attacker.example.com/owner/name.git` (same `https:` protocol as the original).
3. Desktop performs a routine refresh (e.g., via `repositoryWithRefreshedGitHubRepository`, called during normal repository/GitHub sync flows) which calls `api.fetchRepository(owner, name)` and passes the result into `updateRemoteUrl`.
4. Because `protocolsMatch` is true, `remoteUrlUnchanged` is true (user never manually touched the remote), and `!urlsMatch` is true (new host differs), `gitStore.setRemoteURL('origin', 'https://attacker.example.com/owner/name.git')` executes silently — see [8](#0-7) .
5. The user's next `git push` sends commits to `attacker.example.com` instead of the intended repository, with no explicit warning shown beforehand.

Note: I was not able to fully trace every call path that triggers `repositoryWithRefreshedGitHubRepository` in the background (it's referenced 18 times in `app-store.ts`), nor confirm end-to-end whether the credential trampoline would also leak an OAuth token to the new host in this exact flow — that would need deeper tracing of `withTrampolineEnv`/`findGitHubTrampolineAccount` against a non-matching endpoint, which is only partially visible in the indexed code shown here.

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

**File:** app/src/lib/find-account.ts (L38-69)
```typescript
export async function findAccountForRemoteURL(
  urlOrRepositoryAlias: string,
  accounts: ReadonlyArray<Account>,
  canAccessRepository: RepositoryLookupFunc = canAccessRepositoryUsingAPI
): Promise<Account | null> {
  const allAccounts = [...accounts, Account.anonymous()]

  // We have a couple of strategies to try to figure out what account we
  // should use to authenticate the URL:
  //
  //  1. Try to parse a remote out of the URL.
  //    1. If that works, try to find an account for that host.
  //    2. If we don't find an account move on to our next strategy.
  //  2. Try to parse an owner/name.
  //    1. If that works, find the first account that can access it.
  //  3. And if all that fails then throw our hands in the air because we
  //     truly don't care.
  const parsedURL = parseRemote(urlOrRepositoryAlias)
  if (parsedURL) {
    const account =
      allAccounts.find(a => {
        const htmlURL = getHTMLURL(a.endpoint)
        const parsedEndpoint = URL.parse(htmlURL)
        return parsedURL.hostname === parsedEndpoint.hostname
      }) || null

    // If we find an account whose hostname matches the URL to be cloned, it's
    // always gonna be our best bet for success. We're not gonna do better.
    if (account) {
      return account
    }
  }
```

**File:** app/src/ui/repository-settings/remote.tsx (L1-33)
```typescript
import * as React from 'react'
import { IRemote } from '../../models/remote'
import { TextBox } from '../lib/text-box'
import { DialogContent } from '../dialog'

interface IRemoteProps {
  /** The remote being shown. */
  readonly remote: IRemote

  /** The function to call when the remote URL is changed by the user. */
  readonly onRemoteUrlChanged: (url: string) => void
}

/** The Remote component. */
export class Remote extends React.Component<IRemoteProps, {}> {
  public render() {
    const remote = this.props.remote
    return (
      <DialogContent>
        <TextBox
          placeholder="Remote URL"
          label={
            __DARWIN__
              ? `Primary Remote Repository (${remote.name}) URL`
              : `Primary remote repository (${remote.name}) URL`
          }
          value={remote.url}
          onValueChanged={this.props.onRemoteUrlChanged}
        />
      </DialogContent>
    )
  }
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
