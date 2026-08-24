### Title
`updateRemoteUrl` silently rewrites the `origin` remote to an attacker-controlled URL from the GitHub API's `clone_url` field - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
The reported smart-contract bug is a broken invariant: a privileged setter (`setAddress`) can write a "null-like" value that a downstream function (`_updateImpl`) later trusts implicitly, causing it to silently blow away existing state. The GitHub Desktop analog is structurally similar: `updateRemoteUrl` trusts a single field (`clone_url`) from a GitHub API response object and, under an insufficiently strict set of guard conditions, uses it to silently mutate a security-relevant piece of local state (the `origin` git remote URL) via `git remote set-url`, with no host/endpoint validation and no user confirmation.

### Finding Description
`updateRemoteUrl` is invoked from `repositoryWithRefreshedGitHubRepository` in `app/src/lib/stores/app-store.ts` (around line 4906) after calling `api.fetchRepository(owner, name)`. The API response (`IAPIFullRepository`), specifically its `clone_url` field, is attacker-influenceable whenever the attacker controls the API server the repo is hosted on (GitHub Enterprise Server), or otherwise controls the content of the API response for that repository (a malicious/compromised GHES instance, or a MITM'd/misconfigured proxy sitting in front of the API endpoint).

The guard logic in `updateRemoteUrl`: [1](#0-0) 

only checks:
1. `protocolsMatch` — that the URL scheme (https/ssh) of the current remote and the new `clone_url` are the same,
2. `remoteUrlUnchanged` — that the currently-recorded `gitHubRepository.cloneURL` still matches the local `origin` remote (i.e., the user hasn't manually re-pointed `origin`),
3. `!urlsMatch` — that the new `clone_url` differs (by owner/name comparison) from the current remote.

None of these checks validate that the **hostname** of the new `clone_url` matches the expected GitHub endpoint (`account.endpoint`) or the original remote's hostname. `urlMatchesRemote` (in `app/src/lib/repository-matching.ts`, lines 90-118) only compares hostname/owner/name for *equality* between two URLs — it is used to detect a *mismatch* (triggering the rewrite), not to validate that the *new* URL is safe. Once the three guard conditions are met, the code calls: [2](#0-1) 

which executes `gitStore.setRemoteURL(...)`, ultimately running `git remote set-url origin <apiRepo.clone_url>` via `app/src/lib/git/remote.ts`: [3](#0-2) 

This happens with no user prompt, in contrast to the analogous "upstream URL mismatch" flow which *does* prompt the user via a dialog (`app/src/ui/upstream-already-exists/upstream-already-exists.tsx`) before updating the `upstream` remote. The `origin` remote rewrite path has no equivalent confirmation step.

### Impact Explanation
If an attacker controls (or compromises) the API server backing a repository already tracked by Desktop — most plausibly a malicious or compromised GitHub Enterprise Server instance, since GHES admins/operators fully control API responses — they can set `clone_url` in the repository API payload to an arbitrary URL (different host, different credentials-bearing user info, etc.), as long as the URL scheme matches the existing remote's scheme. Desktop will then silently repoint the user's `origin` remote to that attacker-controlled host. Consequences:
- All subsequent `git fetch`/`git pull` operations initiated by the user pull code from the attacker's server, potentially delivering malicious history/content that gets merged into the user's working tree.
- All subsequent `git push` operations send the user's commits (and, depending on the credential helper/trampoline flow keyed off the remote URL's host, potentially credentials/tokens resolved for that host) to the attacker-controlled endpoint — silently corrupting where the user's work is pushed and creating a path to credential/token exposure to an attacker-controlled git host.
- The change is invisible to the user unless they manually inspect `git remote -v`.

This matches the report's "Valid Impact" criteria: attacker controls a GitHub API object, resulting in silent corruption of what the user pushes/fetches and potential credential exposure — no local/physical access, no prior malware, and no unnatural user interaction is required (the refresh runs as part of normal repository-info refresh flows).

### Likelihood Explanation
Moderate-to-low but plausible. It requires the attacker to control the GitHub API responses for a repository the victim has already added to Desktop — realistic in GHES/self-hosted deployments where the attacker is a malicious/compromised server operator, or where a MITM position on the API traffic exists. It does not require the victim to click anything unusual: `repositoryWithRefreshedGitHubRepository` is invoked as part of normal periodic repository/GitHub-info refresh in `app-store.ts`. The `protocolsMatch` condition modestly narrows exploitation (same scheme required), but does not prevent redirecting to a different host under the same scheme.

### Recommendation
In `updateRemoteUrl` (`app/src/lib/stores/updates/update-remote-url.ts`), before calling `gitStore.setRemoteURL`, validate that the new `clone_url`'s hostname matches the expected account endpoint's hostname (or the previous remote's hostname) — not just that the protocol matches. Consider also surfacing a confirmation prompt to the user before silently rewriting `origin`, mirroring the existing `UpstreamAlreadyExists` dialog used for the `upstream` remote.

### Proof of Concept
Conceptual (cannot be executed without a controlled GHES instance/API mock):
1. User adds/clones a repository from a (malicious or later-compromised) GitHub Enterprise Server; Desktop records `gitHubRepository.cloneURL` = `https://ghes.example.com/org/repo.git` and sets `origin` to the same URL.
2. Attacker (server operator) changes the API response for `GET /repos/org/repo` so that `clone_url` = `https://attacker.example.com/org/repo.git` (same `https` scheme).
3. Desktop's periodic refresh calls `repositoryWithRefreshedGitHubRepository`, which calls `updateRemoteUrl(gitStore, gitHubRepository, apiRepo)`.
4. Since `protocolsMatch` is true, `remoteUrlUnchanged` is true (user hasn't touched the remote), and `urlsMatch` is false (different host/owner), Desktop executes `git remote set-url origin https://attacker.example.com/org/repo.git` with no prompt.
5. The next `git fetch`/`git push` by the user targets `attacker.example.com` instead of the legitimate host. [4](#0-3) 
This existing test demonstrates the mechanism already works as designed for benign rename cases (`updates the repository's remote url when the github url changes`), confirming the code path is reachable purely from API-provided `clone_url` changes with no user interaction, which is the same path an attacker-controlled API response would exercise.

### Citations

**File:** app/src/lib/stores/updates/update-remote-url.ts (L12-44)
```typescript
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
