Based on my research, the strongest analog to the Votium `depositRewards()` pattern — "an unvalidated, externally-controlled value is fed directly into a state-changing/privileged operation without checking that it matches an expected safe form" — is the way GitHub Desktop turns a pull-request's `head.repo.clone_url`, taken directly from a GitHub API response object, into a `git remote add` + `git fetch` target without validating that the URL uses an expected, safe transport.

### Title
Unvalidated `clone_url` from GitHub API pull-request object is passed directly to `git remote add`/`fetch` - (File: app/src/lib/stores/app-store.ts)

### Summary
When a user checks out a pull request from a fork, Desktop takes the `head.repo.clone_url` string straight out of the parsed GitHub API response and uses it, unmodified, as the URL for `git remote add`, then immediately fetches that remote. There is no check that the value is a well-formed `https://`/`ssh://`/`git@` URL before it is handed to `git`.

### Finding Description
`Dispatcher.openPullRequestFromUrl` pulls `pullRequest.head.repo.clone_url` straight from the API-parsed pull request object and forwards it to `AppStore._checkoutPullRequest`: [1](#0-0) 

That value flows into `_findPullRequestBranch`, which — if no existing remote matches the URL — calls `addRemote(repository, forkRemoteName, headCloneUrl)` with the raw, un-validated string and then fetches it: [2](#0-1) [3](#0-2) 

`addRemote()` itself performs no validation of the URL scheme/shape before invoking git: [4](#0-3) 

Unlike `parseRemote()` — which is used only for *matching* remotes against known GitHub URL shapes (`app/src/lib/repository-matching.ts`) — there is no equivalent gate applied before the URL is actually used to create a remote and fetch from it. Git supports transports such as `ext::` that can spawn arbitrary local commands as part of a "clone"/"fetch" operation; if the URL is not constrained to expected protocols (http(s)/ssh/git) before being handed to git, a value like `ext::sh -c "..."` placed in the `clone_url` field of the PR object would be executed as part of the fetch. The same unvalidated-URL pattern also occurs for the "convert repository to fork" flow (`app/src/lib/stores/app-store.ts:8981`, `git-store.ts` `addUpstreamRemoteIfNeeded`), which also takes `clone_url` from an API object and calls `setRemoteURL`/`addRemote` directly.

### Impact Explanation
If the URL scheme is not restricted at the point where it is turned into a git remote (I could not confirm from available code whether `envForRemoteOperation()` sets `GIT_ALLOW_PROTOCOL`/`protocol.*.allow` to prevent this — this needs to be verified in a live session), an attacker who can influence the `head.repo.clone_url` field of a pull request object returned to Desktop (e.g., via a compromised/malicious GitHub Enterprise Server, a MITM'd API response, or any other means of injecting an untrusted API payload) could achieve arbitrary command execution on the user's machine the moment Desktop checks out that PR — matching the "attacker controls a GitHub API object → code execution" impact class.

### Likelihood Explanation
This requires the attacker to control or tamper with a GitHub API response (self-hosted GHES compromise, TLS-interception, or supply of a crafted repository/PR the victim opens against a non-github.com endpoint). It is not exploitable purely via github.com's own API (which itself constrains `clone_url` formatting), so likelihood is Low-Medium and contingent on whether existing protocol allow-listing (if any) actually blocks dangerous transports such as `ext::`. This gap should be explicitly verified against `envForRemoteOperation()`.

### Recommendation
Before calling `addRemote`/`setRemoteURL`/fetch with any URL sourced from a GitHub API object (`clone_url`, `ssh_url`, etc.), validate that it matches one of the expected git URL shapes already recognized by `parseRemote()` (`app/src/lib/remote-parsing.ts`), and/or ensure git is invoked with `GIT_ALLOW_PROTOCOL=http:https:ssh:git` (or equivalent `protocol.*.allow=never` config) for all remote-touching operations, rejecting anything else instead of silently trusting the API payload.

### Proof of Concept
1. Point Desktop at a (compromised or malicious) GitHub Enterprise Server endpoint, or intercept its API traffic.
2. Craft a pull request API response whose `head.repo.clone_url` is `ext::sh -c "touch /tmp/pwned"` (or a Windows equivalent) instead of a normal URL.
3. Have the victim use "Checkout this PR" in Desktop, triggering `openPullRequestFromUrl` → `_checkoutPullRequest` → `_findPullRequestBranch`.
4. `addRemote()` adds the malicious URL as a remote (`app/src/lib/git/remote.ts:28-37`), and the subsequent `_fetchRemote` call executes `git fetch` against it (`app/src/lib/stores/app-store.ts:8684-8691`), potentially invoking the `ext::` transport and running the attacker's command.

Note: I was unable to inspect the contents of `envForRemoteOperation()` in this session to confirm or rule out existing `GIT_ALLOW_PROTOCOL` mitigations; this should be checked first in a full session, as it would determine whether this PoC is actually exploitable end-to-end today.

### Citations

**File:** app/src/ui/dispatcher/dispatcher.ts (L2039-2045)
```typescript
    await this.appStore._checkoutPullRequest(
      repository,
      pullRequest.number,
      pullRequest.head.repo.owner.login,
      pullRequest.head.repo.clone_url,
      pullRequest.head.ref
    )
```

**File:** app/src/lib/stores/app-store.ts (L8643-8660)
```typescript
    // Find an existing remote (regardless if set up by us or outside of
    // Desktop).
    let remote = remotes.find(r => urlMatchesRemote(headCloneUrl, r))

    // If we can't find one we'll create a Desktop fork remote.
    if (remote === undefined) {
      try {
        const forkRemoteName = forkPullRequestRemoteName(headRepoOwner)
        remote = await addRemote(repository, forkRemoteName, headCloneUrl)
      } catch (e) {
        this.emitError(
          new Error(
            `Couldn't find PR branch, adding remote failed: ${e.message}`
          )
        )
        return
      }
    }
```

**File:** app/src/lib/stores/app-store.ts (L8682-8691)
```typescript
    // It's quite possible that the PR was created after our last fetch of the
    // remote so let's fetch it and then try again.
    if (existingBranch === undefined) {
      try {
        await this._fetchRemote(repository, remote, FetchType.UserInitiatedTask)
        existingBranch = findRemoteBranch(remoteRef)
      } catch (e) {
        log.error(`Failed fetching remote ${remote?.name}`, e)
      }
    }
```

**File:** app/src/lib/git/remote.ts (L28-37)
```typescript
/** Add a new remote with the given URL. */
export async function addRemote(
  repository: Repository,
  name: string,
  url: string
): Promise<IRemote> {
  await git(['remote', 'add', name, url], repository.path, 'addRemote')

  return { url, name }
}
```
