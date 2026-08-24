### Title
Silent git remote URL rewrite based on owner/name-matched GitHub API data enables repository takeover via name squatting - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
The audited Solidity bug is a "desynchronized authority" pattern: one component (`GTELaunchpadV2PairFactory`) trusts a stale immutable reference while another component (`Launchpad`) has since updated the "real" value, and downstream logic silently uses the wrong address with no cross-check. The GitHub Desktop analog is structurally identical: `Repository.gitHubRepository` is resolved and kept "in sync" purely by **owner login + repository name**, not by the GitHub repository's immutable numeric `id`. When Desktop refreshes a repository's association, it fetches `owner/name` from the API and, if the returned `clone_url` differs from the previously cached one, **silently rewrites the local git remote URL** to whatever the API now says that owner/name pair resolves to — without ever verifying that the underlying repository `id` is unchanged.

### Finding Description
`repositoryWithRefreshedGitHubRepository` in `app/src/lib/stores/app-store.ts` (around lines 4874-4914) resolves the association for a local repository via `matchGitHubRepository(repository)`, which yields an `{ account, owner, name }` tuple based on the current git remote URL. It then calls: [1](#0-0) 

```
const { account, owner, name } = match
const api = API.fromAccount(account)
const apiRepo = await api.fetchRepository(owner, name)
...
if (repository.gitHubRepository) {
  const gitStore = this.gitStoreCache.get(repository)
  await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
}
```

`fetchRepository(owner, name)` performs a live lookup keyed only by `owner/name`, not by any persisted repository `id`. The result is passed into `updateRemoteUrl`: [2](#0-1) 

```
export async function updateRemoteUrl(
  gitStore: GitStore,
  gitHubRepository: GitHubRepository,
  apiRepo: IAPIRepository
): Promise<void> {
  ...
  const remoteUrl = gitStore.defaultRemote.url
  const updatedRemoteUrl = apiRepo.clone_url
  const urlsMatch = urlMatchesRemote(updatedRemoteUrl, gitStore.defaultRemote)
  ...
  const remoteUrlUnchanged =
    gitStore.defaultRemote &&
    urlMatchesRemote(gitHubRepository.cloneURL, gitStore.defaultRemote)

  if (protocolsMatch && remoteUrlUnchanged && !urlsMatch) {
    await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
  }
}
```

The only "guard" (the exact analog of the audit's missing sync check) is `remoteUrlUnchanged`: it verifies the *local git remote* still matches the *last cached* `gitHubRepository.cloneURL` — i.e., that the user hasn't manually repointed their remote. It does **not** verify that the API object being trusted (`apiRepo`) actually refers to the same underlying repository (`apiRepo.id === gitHubRepository`'s persisted `id`). Because GitHub allows repository names to be freed and reused (deleted repos, renamed repos, or repos removed from an org), an attacker who creates a new repository under the *same owner/name* the victim's local remote was pointing at will cause `api.fetchRepository(owner, name)` to return the attacker's repository data. `remoteUrlUnchanged` still holds (the user's remote still points to the *old* legitimate `clone_url`), `urlsMatch` is false (attacker's `clone_url` differs), and protocol matches — so the code silently rewrites the user's local git remote to the attacker-controlled clone URL.

Persistence of this identity confusion is reinforced by `repositories-store.ts`, which also looks up/creates `GitHubRepository` records keyed by `[ownerID+name]` rather than the GitHub numeric repository `id`: [3](#0-2) 

```
const existingRepo = await this.db.gitHubRepositories
  .where('[ownerID+name]')
  .equals([owner.id, gitHubRepository.name])
  .first()
```

So the entire chain — matching, API fetch, remote-URL sync, and DB upsert — treats `owner/name` as the durable identity of a repository, when in reality that identity is only durable as long as no one else can claim the same name after it becomes available. This is directly analogous to the audit finding: the "trusted" reference (`launchpadLp` in the factory vs. `launchpadLPVault` in `Launchpad`) can silently diverge because the invariant "these two must always refer to the same target" is never actually checked at the point of use.

### Impact Explanation
If exploited, subsequent `git push`/`git fetch`/`git pull` operations performed by Desktop against the rewritten remote would silently interact with an attacker-controlled repository:
- Pushes would leak the victim's commits/code to the attacker's server instead of (or in addition to) the intended destination.
- Fetches/pulls would pull attacker-controlled refs and objects into the victim's local repository, which could be leveraged for further supply-chain-style tampering (e.g., malicious commits merged unknowingly, since the UI still shows the same "repository name" the user recognizes).
- Because `updateRemoteUrl` operates on `gitStore.defaultRemote` transparently as part of a background refresh, this happens with no explicit user prompt or confirmation — it's a silent corruption of what the user is pushing to / pulling from, matching the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
Triggering this requires only unprivileged conditions the report scope allows: an attacker registers a repository under an owner/name combination that a target's stale local `GitHubRepository` record still references (e.g., after the original repo was deleted, renamed away, or transferred, freeing the name). This does not require compromising the target's machine, credentials, or any social engineering step beyond ordinary GitHub repo creation — it's purely a server-side object (the GitHub API repository record) that the attacker fully controls. Desktop performs this refresh automatically in the background (`repositoryWithRefreshedGitHubRepository` is part of routine repository refresh flow), so no unusual user action is needed beyond Desktop periodically syncing repository metadata for a repository the user already has cloned.

### Recommendation
Before trusting `apiRepo.clone_url` to update the local remote, verify that `apiRepo.id` matches the numeric GitHub repository `id` already persisted for `gitHubRepository` (its `dbID`/underlying API id), not just `owner/name`. If the ids differ, treat this as "repository identity changed" (e.g., deleted-and-recreated, or name squatted) and require explicit user confirmation before rewriting the remote, rather than silently updating it. The same id-based check should be applied in `repositories-store.ts`'s `_upsertGitHubRepository`/`upsertGitHubRepositoryFromMatch` lookups, which currently key exclusively on `[ownerID+name]`.

### Proof of Concept
1. Victim clones `https://github.com/owner/repo-name` (call this Repo A, id=111) in Desktop; Desktop persists `GitHubRepository{name: "repo-name", ownerID, cloneURL: "https://github.com/owner/repo-name.git"}` keyed by `[ownerID, "repo-name"]`.
2. Repo A is deleted or renamed on GitHub, freeing `owner/repo-name`. (Note: in the org-transfer/rename case, no deletion by the legitimate owner is even needed for the account itself if the org allows name churn; the essential requirement is that the name becomes reusable and the attacker acquires it.)
3. Attacker creates a new repository at the same `owner/repo-name` path (Repo B, id=999) with a different `clone_url` if hosted elsewhere, or simply relies on `clone_url` differing due to a different id/slug internally — regardless, `apiRepo.id !== 111`.
4. Desktop performs a routine background refresh; `matchGitHubRepository` resolves `{owner: "owner", name: "repo-name"}` from the victim's still-unchanged local remote URL, and `api.fetchRepository("owner", "repo-name")` now returns Repo B's data.
5. In `updateRemoteUrl`, `remoteUrlUnchanged` is `true` (victim's remote still equals the last cached Repo A `cloneURL`), `urlsMatch` is `false` (Repo B's `clone_url` differs), protocols match → `gitStore.setRemoteURL(...)` is invoked, silently repointing the victim's local `origin` remote to Repo B without any user-visible warning that the underlying repository identity changed. [4](#0-3)

### Citations

**File:** app/src/lib/stores/app-store.ts (L4887-4907)
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

**File:** app/src/lib/stores/repositories-store.ts (L613-616)
```typescript
    const existingRepo = await this.db.gitHubRepositories
      .where('[ownerID+name]')
      .equals([owner.id, gitHubRepository.name])
      .first()
```
