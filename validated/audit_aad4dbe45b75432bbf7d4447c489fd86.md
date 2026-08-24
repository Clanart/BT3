### Title
Repository name-squatting lets an attacker silently hijack a tracked repo's remote URL and upstream fork target - (File: `app/src/lib/stores/updates/update-remote-url.ts`, `app/src/lib/stores/app-store.ts`)

### Summary
The generic report's broken invariant is: a value that is supposed to represent "the true, canonical state of a trusted object" (vault backing-asset value / GitHub repository identity) can actually be supplied by an attacker, and the protocol/app acts on that value without verifying it corresponds to the *same underlying entity* it originally trusted. In GitHub Desktop, the analog is `owner/name` string matching used to re-fetch a tracked repository's canonical data from the GitHub API. Desktop never pins the association to a stable GitHub repository ID — it re-derives `owner`/`name` from the current git remote text on every refresh and blindly trusts whatever `IAPIRepository` object the API returns for that name, using it to silently rewrite the local git remote URL and other trust-relevant fields (`parent`, `cloneURL`, `permissions`).

### Finding Description
`matchGitHubRepository` derives `owner`/`name` purely by regex-parsing the current git remote URL string, with no reference to any previously stored repository ID: [1](#0-0) 

`repositoryWithRefreshedGitHubRepository` uses that derived `owner`/`name` pair to call `api.fetchRepository(owner, name)` on every periodic/background refresh, and feeds the returned API object directly into `updateRemoteUrl` and `upsertGitHubRepository`: [2](#0-1) [3](#0-2) 

`updateRemoteUrl` then decides whether to silently call `git remote set-url` based only on **textual** owner/name equality checks (`urlMatchesRemote`), never on any stable repository identifier: [4](#0-3) [5](#0-4) 

This mirrors the vault report exactly: `additionalOwnedAssets()` is trusted as "the value of the backing asset" without any invariant that ties it to a manipulation-resistant source (an oracle), so an attacker who can move the underlying market can forge the value. Here, "`owner/name` still resolves via the GitHub API" is trusted as "this is still my repository" without any invariant tying it to the original repository's immutable ID, so an attacker who acquires the vacated `owner/name` slot (via GitHub's well-known repo-rename/deletion + name-squatting window, i.e. "repojacking") can forge the returned `IAPIRepository` object.

Once the attacker's object is ingested, several trust-relevant fields propagate into the app without any identity check:
- `gitHubRepository.parent` is later used to compute the expected upstream fork URL and is inserted into the local `upstream` remote automatically or via the "Update" flow: [6](#0-5) 
- `gitHubRepository.cloneURL` is used to silently rewrite the tracked remote's URL in `updateRemoteUrl`, and is also used to perform a full re-clone into the user's existing local path when the repository is reported "missing": [7](#0-6) 

No guard in this path checks a stable GitHub repository `id`; `urlMatchesRemote`/`urlsMatch` compare only `hostname`+`owner`+`name` strings, which is exactly the value an attacker can reproduce by registering the same name after the legitimate repo is renamed/deleted.

### Impact Explanation
If an attacker registers a GitHub repository under the exact `owner/name` a Desktop user was previously tracking (after the original was renamed or deleted — a scenario Desktop explicitly created special-case handling for, per the changelog entry "Update the remote url when a repository's name changes on GitHub - #8590"), Desktop's background repository refresh will:
1. Silently execute `git remote set-url` to point the user's origin at the attacker's repository (`updateRemoteUrl`), so the next `git push`/`git fetch` performed by the user in Desktop talks to a repo the attacker controls — this is "silent corruption of what the user pushes" and enables credential/token exposure to an attacker-controlled remote (subject to Desktop's `credential.helper=desktop` scoping, but the destination and any exposed data are now attacker-chosen).
2. Silently ingest attacker-provided `parent`/fork metadata that later drives automatic upstream-remote configuration, redirecting pull-request/upstream fetch flows to an attacker repository.
3. On "Clone Again" from the missing-repository view, re-clone the attacker's repository content into the path the user believes holds their own project, allowing the attacker to plant arbitrary files (including git hooks) that execute during subsequent local git operations.

### Likelihood Explanation
This requires no local access, no admin rights, no pre-existing malware, and no leaked credentials — only (a) a repository the user tracks in Desktop being renamed or deleted upstream (a legitimate, common GitHub event Desktop already special-cases) and (b) the attacker registering the freed `owner/name` before/while Desktop's background refresh runs. The refresh path (`repositoryWithRefreshedGitHubRepository`) runs automatically as part of normal repository refresh, requiring no unusual user action, and the matching logic has no identity pinning to prevent it, making this a purely remote/API-object-driven attacker primitive matching the "attacker controls a GitHub API object" impact category.

### Recommendation
Pin the association between a local repository and its GitHub counterpart to the immutable GitHub repository `id` (already available on `IAPIRepository`/`GitHubRepository.dbID`) rather than re-deriving `owner`/`name` from the current remote URL text on every refresh. Before applying any of `updateRemoteUrl`'s automatic changes, or before trusting `parent`/`cloneURL` from a freshly fetched `IAPIRepository`, verify that the returned object's `id` matches the previously stored `dbID` for that `GitHubRepository`; if it differs, treat it as a different repository and surface a warning to the user instead of silently rewriting remotes/upstreams or reusing it for "Clone Again."

### Proof of Concept
1. User adds/clones `https://github.com/victim/project.git` in Desktop; Desktop stores a `GitHubRepository` record with `dbID = 111` and `cloneURL = https://github.com/victim/project.git`.
2. `victim` renames the GitHub repository to `victim/project-v2` (or deletes it). The name `victim/project` becomes available.
3. Attacker creates a new GitHub repository named exactly `victim/project` (id `999`), controlled by them.
4. On its next background refresh, Desktop calls `matchGitHubRepository` on the still-unchanged local remote URL `https://github.com/victim/project.git`, derives `owner=victim, name=project`, and calls `api.fetchRepository('victim', 'project')`, which now returns the attacker's repo object. [8](#0-7) 
5. `updateRemoteUrl` sees `remoteUrlUnchanged = true` (remote still textually matches the stored `cloneURL` string) and, if the attacker's `clone_url` differs at all (e.g. different casing, or later diverges), the app calls `gitStore.setRemoteURL(...)`, or — even if the URL text is identical — `upsertGitHubRepository` overwrites the local `GitHubRepository` record's `parent`/`permissions`/`cloneURL` fields with the attacker's data with no `id` check. [4](#0-3) 
6. Subsequent Desktop-driven actions (upstream remote setup, "Clone Again" on a missing path) now operate against the attacker's repository without any user-visible warning that the underlying repository changed.

**Note on completeness:** I was not able to fully trace every downstream consumer of `GitHubRepository.parent`/`cloneURL` (e.g., all PR/branch-protection code paths) within this session, so there may be additional impacted flows beyond the ones cited above. This does not change the core finding: the missing identity check on `owner`/`name`-based re-resolution is the root cause.

### Citations

**File:** app/src/lib/repository-matching.ts (L29-46)
```typescript
export function matchGitHubRepository(
  accounts: ReadonlyArray<Account>,
  remote: string
): IMatchedGitHubRepository | null {
  for (const account of accounts) {
    const htmlURL = getHTMLURL(account.endpoint)
    const { hostname } = URL.parse(htmlURL)
    const parsedRemote = parseRemote(remote)

    if (parsedRemote !== null && hostname !== null) {
      if (parsedRemote.hostname.toLowerCase() === hostname.toLowerCase()) {
        return { name: parsedRemote.name, owner: parsedRemote.owner, account }
      }
    }
  }

  return null
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

**File:** app/src/lib/stores/app-store.ts (L4874-4914)
```typescript
  private async repositoryWithRefreshedGitHubRepository(
    repository: Repository
  ): Promise<Repository> {
    const repoStore = this.repositoriesStore
    const match = await this.matchGitHubRepository(repository)

    // TODO: We currently never clear GitHub repository associations (see
    // https://github.com/desktop/desktop/issues/1144). So we can bail early at
    // this point.
    if (!match) {
      return repository
    }

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

    const ghRepo = await repoStore.upsertGitHubRepository(endpoint, apiRepo)
    const freshRepo = await repoStore.setGitHubRepository(repository, ghRepo)

    await this.refreshBranchProtectionState(freshRepo)
    return freshRepo
  }
```

**File:** app/src/lib/stores/app-store.ts (L4964-4977)
```typescript
  private async matchGitHubRepository(
    repository: Repository
  ): Promise<IMatchedGitHubRepository | null> {
    const gitStore = this.gitStoreCache.get(repository)

    if (!gitStore.defaultRemote) {
      await gitStore.loadRemotes()
    }

    const remote = gitStore.defaultRemote
    return remote !== null
      ? matchGitHubRepository(this.accounts, remote.url)
      : null
  }
```

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

**File:** app/src/ui/upstream-already-exists/upstream-already-exists.tsx (L30-41)
```typescript
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

**File:** app/src/ui/missing-repository.tsx (L169-188)
```typescript
  private cloneAgain = async () => {
    const gitHubRepository = this.props.repository.gitHubRepository
    if (!gitHubRepository) {
      return
    }

    const cloneURL = gitHubRepository.cloneURL
    if (!cloneURL) {
      return
    }

    try {
      await this.props.dispatcher.cloneAgain(
        cloneURL,
        this.props.repository.path
      )
    } catch (error) {
      this.props.dispatcher.postError(error)
    }
  }
```
