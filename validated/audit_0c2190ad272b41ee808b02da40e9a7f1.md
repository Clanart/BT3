### Title
Silent auto-migration of `origin`/`upstream` remotes based on mutable owner/name lookup enables GitHub "repo-squatting" hijack of push/fetch destination - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
GitHub Desktop periodically re-resolves the GitHub API repository object for a locally tracked repo by **owner/name string**, not by GitHub's stable numeric repository ID, and then uses that API response to silently rewrite the local `origin` remote (`updateRemoteUrl`) and to silently create/point the `upstream` remote at `parent.cloneURL` (`addUpstreamRemoteIfNeeded`). Because the local `GitHubRepository` model explicitly does **not** persist GitHub's true API id (`dbID` is only a local DB id), there is no invariant tying a locally-known repository to a specific, immutable remote identity. If the owner/name path is freed up (rename, transfer, deletion) and re-registered by an attacker before/instead of the legitimate repo, Desktop's background refresh logic will silently adopt the attacker's `clone_url`/`parent.cloneURL` as the new remote target for a repository the user already trusts and has push access configured for.

### Finding Description
`repositoryWithRefreshedGitHubRepository` (`app/src/lib/stores/app-store.ts:4874-4914`) resolves the API repository purely from `matchGitHubRepository`, which matches accounts/remotes by parsed **owner/name** from the local git remote URL [1](#0-0) , then calls:

```
const apiRepo = await api.fetchRepository(owner, name)
``` [2](#0-1) 

This is a `GET repos/{owner}/{name}` call keyed on the (mutable) path, not a durable node/database ID. The result is fed into `updateRemoteUrl`: [3](#0-2) 

The function only guards against protocol changes and against the user having manually customized the remote (`remoteUrlUnchanged`), but never validates that the API object returned for `owner/name` is *the same underlying repository* the user originally added (no comparison of a stable repo id). If it isn't — because the name was vacated and re-claimed by someone else — the check `remoteUrlUnchanged && !urlsMatch` still evaluates as intended and Desktop calls `gitStore.setRemoteURL(...)` to silently rewrite `origin` to the new (attacker-controlled) `clone_url`, invoking: [4](#0-3) 

This same class of trust also drives `addUpstreamRemoteIfNeeded`, which unconditionally adds an `upstream` remote using the API-reported `parent.cloneURL` whenever a repository's GitHub metadata says it has a fork parent — again with no verification that the parent identity is stable: [5](#0-4) 

Both code paths run automatically on ordinary user actions: `repositoryWithRefreshedGitHubRepository` is invoked from `_fetch`/`_fetchRemote`/`_fetchRefspec`/`performPush` flows via `withRefreshedGitHubRepository`, and `addUpstreamRemoteIfNeeded` runs on every repository selection (`_selectRepositoryRefreshTasks`), so no explicit user gesture beyond "open the repo" or "fetch/pull/push" is required.

`GitHubRepository.dbID` is explicitly documented as unrelated to the GitHub API id, confirming there is no persisted stable-identity anchor to detect repository substitution: [6](#0-5) 

The upsert path that stores the API data locally (`_upsertGitHubRepository`) likewise keys off `[ownerID+name]` and overwrites `cloneURL`, `htmlURL`, `parentID`, and `permissions` with whatever the API currently reports for that name, with no cross-check against a previous immutable id: [7](#0-6) 

### Impact Explanation
This maps directly onto the "rewards squatting" bug class from the report: an economic/identity value (there: the current reward token; here: the GitHub repository behind an owner/name path) is trusted to remain semantically continuous across time, and the system automatically migrates state (there: reward accrual token; here: git remote URL) based on that untrusted external signal without an invariant that binds to a durable identifier. The Desktop analog satisfies the "Valid Impact" criteria: the attacker controls a GitHub API object (a squatted repository at a freed owner/name), and the result is **silent corruption of what the user pushes/fetches** — future `git push`/`git fetch`/`git pull` on that remote (`origin` or `upstream`) can be silently redirected to an attacker-controlled repository without any prompt, dialog, or diff review. Depending on downstream flows (fetch pulling attacker refs into local branches, or push exfiltrating code/history to the attacker's repo), this can lead to code exfiltration or supply-chain corruption of what gets shared.

### Likelihood Explanation
Requires: (1) a tracked repository whose owner/name becomes available for re-registration (organization/user rename, repo rename, repo deletion, or account deletion/renaming — all routine, attacker-triggerable-adjacent events on GitHub, and "repojacking" of freed names is a well-documented real-world technique), and (2) the victim performing a routine fetch/pull/push/repository-select after the squat but before manually noticing the remote no longer resolves to the expected project. No local access, no malware, and no unusual user action are required beyond normal app usage (open repo, fetch, push) — matching the "unprivileged... attacker controls a git remote/proxy response" acceptance criteria. The main uncertainty is timing (attacker must win the race to claim the freed name before the user's own client re-syncs the correct migration), which is a real-world but not certain scenario.

### Recommendation
- Persist and verify GitHub's own immutable repository `id`/`node_id` for every locally tracked `GitHubRepository`, and refuse to silently apply `updateRemoteUrl`/`addUpstreamRemoteIfNeeded` changes when the freshly fetched API object's id does not match the previously stored id.
- When an owner/name lookup returns a different `id` than previously recorded, treat this as a repository-identity change: surface an explicit warning/confirmation dialog to the user (similar to the existing `UpstreamAlreadyExists` dialog) rather than auto-rewriting the remote.
- Apply the same identity check before using `parent.cloneURL` in `addUpstreamRemoteIfNeeded`.

### Proof of Concept
1. User A clones `github.com/acme/pretty-lib` in GitHub Desktop; Desktop stores a `GitHubRepository` record for `acme/pretty-lib` (no stable API id retained, per `app/src/models/github-repository.ts:15-22`).
2. The `acme` org (or repo) is renamed/deleted, freeing the `acme/pretty-lib` path (or, if `pretty-lib` is renamed to something else, the *old* name becomes available once GitHub's redirect eventually lapses).
3. An attacker registers a new repository at the freed `acme/pretty-lib` path, setting its own `clone_url` and/or a malicious `parent` (fork) pointing to `attacker/malicious-repo`.
4. User A performs an ordinary `fetch`/`pull`/`push`, or simply reselects the repo in Desktop's sidebar. `repositoryWithRefreshedGitHubRepository` resolves `owner=acme, name=pretty-lib` via `matchGitHubRepository` and calls `api.fetchRepository('acme','pretty-lib')`, which now returns the attacker's repository object [8](#0-7) .
5. `updateRemoteUrl` compares the previously stored `cloneURL` (still textually `acme/pretty-lib`) against the current remote (match) and the new `apiRepo.clone_url` (may differ if attacker used a different canonical form/protocol), and if different, silently calls `gitStore.setRemoteURL('origin', <attacker clone_url>)` — or, if the attacker's repo metadata declares a `parent`, `addUpstreamRemoteIfNeeded` silently adds an `upstream` remote pointing at `parent.cloneURL` — with no user prompt.
6. Subsequent pushes/fetches by User A now interact with attacker-controlled infrastructure without any visible change in Desktop's UI beyond the remote URL text (which most users never inspect).

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

**File:** app/src/lib/api.ts (L972-988)
```typescript
  /** Fetch a repo by its owner and name. */
  public async fetchRepository(
    owner: string,
    name: string
  ): Promise<IAPIFullRepository | null> {
    try {
      const response = await this.ghRequest('GET', `repos/${owner}/${name}`)
      if (response.status === HttpStatusCode.NotFound) {
        log.warn(`fetchRepository: '${owner}/${name}' returned a 404`)
        return null
      }
      return await parsedResponse<IAPIFullRepository>(response)
    } catch (e) {
      log.warn(`fetchRepository: an error occurred for '${owner}/${name}'`, e)
      return null
    }
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

**File:** app/src/lib/stores/git-store.ts (L1321-1356)
```typescript
  public async addUpstreamRemoteIfNeeded(): Promise<void> {
    const parent =
      this.repository.gitHubRepository &&
      this.repository.gitHubRepository.parent
    if (!parent) {
      return
    }

    const remotes = await getRemotes(this.repository)
    const upstream = findUpstreamRemote(parent, remotes)
    if (upstream) {
      return
    }

    const remoteWithUpstreamName = remotes.find(
      r => r.name === UpstreamRemoteName
    )
    if (remoteWithUpstreamName) {
      const error = new UpstreamAlreadyExistsError(
        this.repository,
        remoteWithUpstreamName
      )
      this.emitError(error)
      return
    }

    const url = forceUnwrap(
      'Parent repositories are fully loaded',
      parent.cloneURL
    )

    this._upstreamRemote =
      (await this.performFailableOperation(() =>
        addRemote(this.repository, UpstreamRemoteName, url)
      )) ?? null
  }
```

**File:** app/src/models/github-repository.ts (L15-22)
```typescript
  public constructor(
    public readonly name: string,
    public readonly owner: Owner,
    /**
     * The ID of the repository in the app's local database. This is no relation
     * to the API ID.
     */
    public readonly dbID: number,
```

**File:** app/src/lib/stores/repositories-store.ts (L596-666)
```typescript
  private async _upsertGitHubRepository(
    endpoint: string,
    gitHubRepository: IAPIRepository | IAPIFullRepository,
    ignoreParent = false
  ): Promise<GitHubRepository> {
    const parent =
      'parent' in gitHubRepository && gitHubRepository.parent !== undefined
        ? await this._upsertGitHubRepository(
            endpoint,
            gitHubRepository.parent,
            true
          )
        : await Promise.resolve(null) // Dexie gets confused if we return null

    const { login, type } = gitHubRepository.owner
    const owner = await this.putOwner(endpoint, login, type)

    const existingRepo = await this.db.gitHubRepositories
      .where('[ownerID+name]')
      .equals([owner.id, gitHubRepository.name])
      .first()

    // If we can't resolve permissions for the current repository chances are
    // that it's because it's the parent repository of another repository and we
    // ended up here because the "actual" repository is trying to upsert its
    // parent. Since parent repository hashes don't include a permissions hash
    // and since it's possible that the user has both the fork and the parent
    // repositories in the app we don't want to overwrite the permissions hash
    // in the parent repository if we can help it or else we'll end up in a
    // perpetual race condition where updating the fork will clear the
    // permissions on the parent and updating the parent will reinstate them.
    const permissions =
      getPermissionsString(gitHubRepository) ??
      existingRepo?.permissions ??
      undefined

    // If we're told to ignore the parent then we'll attempt to use the existing
    // parent and if that fails set it to null. This happens when we want to
    // ensure we have a GitHubRepository record but we acquired the API data for
    // said repository from an API endpoint that doesn't include the parent
    // property like when loading pull requests. Similarly even when retrieving
    // a full API repository its parent won't be a full repo so we'll never know
    // if the parent of a repository has a parent (confusing, right?)
    //
    // We do all this to ensure that we only set the parent to null when we know
    // that it needs to be cleared. Otherwise we could have a scenario where
    // we've got a repository network where C is a fork of B and B is a fork of
    // A which is the root. If we attempt to upsert C without these checks in
    // place we'd wipe our knowledge of B being a fork of A.
    //
    // Since going from having a parent to not having a parent is incredibly
    // rare (deleting a forked repository and creating it from scratch again
    // with the same name or the parent getting deleted, etc) we assume that the
    // value we've got is valid until we're certain its not.
    const parentID = ignoreParent
      ? existingRepo?.parentID ?? null
      : parent?.dbID ?? null

    const updatedGitHubRepo: IDatabaseGitHubRepository = {
      ...(existingRepo?.id !== undefined && { id: existingRepo.id }),
      ownerID: owner.id,
      name: gitHubRepository.name,
      private: gitHubRepository.private,
      htmlURL: gitHubRepository.html_url,
      cloneURL: gitHubRepository.clone_url,
      parentID,
      lastPruneDate: existingRepo?.lastPruneDate ?? null,
      issuesEnabled: gitHubRepository.has_issues,
      isArchived: gitHubRepository.archived,
      permissions,
    }
```

**File:** app/src/lib/stores/app-store.ts (L4874-4907)
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
```
