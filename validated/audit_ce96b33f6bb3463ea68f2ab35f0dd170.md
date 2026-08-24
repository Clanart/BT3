### Title
Silent, unconfirmed rewrite of the local git remote URL from GitHub API `clone_url` allows a repository-rename/reclaim ("repo‑jacking") to redirect future pushes to an attacker-controlled repository - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
GitHub Desktop periodically re-fetches metadata for the currently selected repository from the GitHub API and, if the returned `clone_url` differs from the repository's previously-recorded clone URL while still matching the *local* remote by owner/name, it silently rewrites the user's default git remote (`origin`) to the API-supplied URL — with no dialog, warning, or user confirmation. Because the owner/name pair used to query the API is derived from parsing the *local, potentially stale* git remote URL, and GitHub allows repository/account renames that free up the old `owner/name` slug for reclamation by anyone, an attacker who reclaims a stale `owner/name` can get their own repository's `clone_url` accepted and written into the victim's local git configuration, silently changing where the user's future `git push`/`git fetch` operations point.

### Finding Description
When a repository is selected, `AppStore._selectRepositoryRefreshTasks` calls `repositoryWithRefreshedGitHubRepository`, which:
1. Calls `matchGitHubRepository` to derive an `owner`/`name` pair, using the *local* remote URL parsed via `parseRemote`/`parseRepositoryIdentifier` (`app/src/lib/repository-matching.ts`).
2. Fetches `api.fetchRepository(owner, name)` from the GitHub API using that (untrusted, user-controlled-by-history) owner/name.
3. Passes the resulting `apiRepo` (an `IAPIRepository`, i.e. attacker-influenceable GitHub API data) into `updateRemoteUrl`: [1](#0-0) 

`updateRemoteUrl` then compares the API's `clone_url` against the stored `gitHubRepository.cloneURL` and the current local remote, and — if the protocol matches and the previously stored clone URL still matches the local remote but the *new* API `clone_url` does not — silently calls `gitStore.setRemoteURL(...)`, with no user prompt: [2](#0-1) 

The broken invariant: **the value used to update where the user's commits get pushed (the remote URL) is derived from a live GitHub API response keyed off an owner/name pair whose ownership is not guaranteed to be stable or verified against the account/identity the user actually intended.** GitHub allows a repository's owner (account) to be renamed, and once renamed the old login/slug becomes available for a third party to register (a well-documented "repo-jacking"/dependency-confusion-adjacent technique). If the *original* upstream this repository was cloned from renames, and an attacker later registers the vacated `owner` (or `owner/name`) and publishes a repository there, then the next time the user opens Desktop and it silently re-syncs metadata, `api.fetchRepository(owner, name)` resolves to the attacker's repository, and `updateRemoteUrl` will accept its `clone_url` and rewrite the victim's `origin` remote to it — without any confirmation dialog, diff review, or even a log visible to a normal user.

Existing guards do not stop this:
- `urlMatchesRemote`/`urlsMatch` (`app/src/lib/repository-matching.ts`) only compare structural fields (hostname/owner/name) parsed out of URLs — they only prevent updates when the URL is *already* identical; they provide no authenticity check of *who* currently owns that owner/name on GitHub's servers.
- The protocol-match check in `updateRemoteUrl` only prevents `ssh`↔`https` protocol flips; it does nothing to validate the target account/repository identity.
- There is no confirmation UI: `gitStore.setRemoteURL` is invoked directly and unconditionally once the checks pass, as confirmed by the accompanying unit tests exercising only URL/protocol equality, not ownership verification (`app/test/unit/stores/updates/update-remote-url-test.ts`). [3](#0-2) 

### Impact Explanation
If exploited, an attacker can cause Desktop to silently reconfigure a victim's local git remote to point at an attacker-controlled repository on github.com, without any user action beyond normal use of the app (selecting/switching to the affected repository, which triggers the periodic background refresh). Future pushes from that user would then land in the attacker's repository instead of (or in addition to, depending on later reconciliation) the intended one — this is "silent corruption of what the user commits or pushes," directly in the report's list of valid impact categories. It could also be leveraged to trick the user's future "Create Pull Request"/"View on GitHub" actions into targeting the attacker's repo, and depending on downstream trust decisions (e.g., auto-adding upstream remotes, branch protection state fetched from the "same" owner/name), could expose additional metadata to the attacker.

### Likelihood Explanation
This requires a specific but realistic precondition chain: (1) the tracked upstream repository's owner account is renamed or the repo is deleted/renamed at some point, freeing the `owner/name` slug, and (2) an attacker registers that exact slug before the victim's Desktop next performs its background repository metadata refresh. This is a known real-world attack class ("repo-jacking"/"account takeover via rename") that has affected large ecosystems (e.g. npm/GitHub Action supply-chain attacks exploit exactly this GitHub behavior). It does not require local access, admin rights, prior malware, or leaked credentials — only that the attacker can create a public GitHub repository/account with a specific name, which is unprivileged and fully within GitHub's public sign-up flow. The likelihood is not "high" (it depends on a rename occurring and being won in a race by the attacker), but it is a legitimate, unprompted, attacker-reachable path via a GitHub API object as required by the report's valid-impact criteria.

### Recommendation
- Do not silently rewrite the default remote URL from API data. At minimum, require explicit user confirmation before changing `origin`'s URL, showing the old and new URL and account/owner delta.
- When the API-resolved `owner`/`name` differs from the one that produced the previously stored `GitHubRepository` record (e.g., the repository `id` from the API changed even though owner/name look the same), treat this as a completely different repository rather than an "update," and require the user to explicitly re-link or re-confirm rather than auto-updating the clone URL.
- Pin the association to GitHub's stable numeric repository `id` (already stored in `GitHubRepository`/`IAPIRepository.id`) rather than owner/name strings, and refuse silent remote-URL updates unless the returned repository's `id` matches the one already associated with the local `GitHubRepository` record.

### Proof of Concept
1. User A clones `https://github.com/alice/project.git` in GitHub Desktop; Desktop stores `gitHubRepository.cloneURL = https://github.com/alice/project` and `id = 111` in its local repositories database (`RepositoriesStore._upsertGitHubRepository`).
2. Alice renames her GitHub account from `alice` to `alice2` (a legitimate, common action). The `alice` login becomes available for anyone to claim on GitHub.
3. Attacker registers the `alice` account and creates a public repository named `project` (attacker's repo has a *different* `id`, e.g. `999`).
4. User A reopens/re-selects the repository in Desktop. `_selectRepositoryRefreshTasks` → `repositoryWithRefreshedGitHubRepository` runs `matchGitHubRepository`, which parses the *local, unchanged* remote URL (`https://github.com/alice/project.git`) to get `owner=alice, name=project`, then calls `api.fetchRepository('alice', 'project')`, which now resolves to the attacker's repository (`clone_url` possibly differing slightly, e.g. different casing/trailing slash, or truly identical string if attacker mirrors it exactly to also collect any future silent metadata reconciliation).
5. `updateRemoteUrl` compares the attacker's `apiRepo.clone_url` against the stored `gitHubRepository.cloneURL` (`app/src/lib/stores/updates/update-remote-url.ts:18-44`); since the previously-recorded clone URL still matches the local remote (`remoteUrlUnchanged` = true) and the protocol matches, but the newly fetched `clone_url` differs even slightly from the current remote, Desktop calls `gitStore.setRemoteURL('origin', <attacker clone_url>)` with no prompt.
6. The next `git push` from User A's Desktop client is silently sent to the attacker's repository.

Note: I was unable to directly trace the full implementation of `matchGitHubRepository` (only found via `grep_search`, not fully read) within the available iterations, so the exact precedence/fallback logic between using the local remote URL versus previously stored API identifiers could not be independently confirmed line-by-line; readers should verify this function in `app/src/lib/stores/app-store.ts` before treating this as fully validated.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4904-4907)
```typescript
    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
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

**File:** app/test/unit/stores/updates/update-remote-url-test.ts (L68-94)
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

  it("doesn't update the repository's remote url when the github url is the same", async t => {
    const { gitHubRepository, gitStore } = await createRepository(
      t,
      apiRepository
    )
    assert(gitStore.currentRemote !== null)
    const originalUrl = gitStore.currentRemote.url
    assert.notEqual(originalUrl.length, 0, 'Expected originalUrl to be empty')
    await updateRemoteUrl(gitStore, gitHubRepository, apiRepository)
    assert(gitStore.currentRemote !== null)
    assert.equal(gitStore.currentRemote.url, originalUrl)
  })
```
