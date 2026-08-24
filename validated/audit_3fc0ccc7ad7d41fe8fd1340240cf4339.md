## Finding [1](#0-0) 

### Title
Desktop silently rewrites the user's `origin` remote URL based on an untrusted GitHub API `clone_url`, only checking that the URL *scheme* matches - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl` automatically calls `gitStore.setRemoteURL()` to overwrite the user's local git remote whenever the GitHub API's `clone_url` for a repository differs from what's configured locally. The only safety checks are (1) that the previous local remote still matched the *previously cached* API clone URL, and (2) that the URL *protocol string* ("https:" vs "https:") is unchanged. Nothing verifies that the new host/owner/name is a trustworthy successor of the original repository (e.g., same hostname), so a malicious or compromised GitHub API response can silently redirect the user's remote to an attacker-controlled server.

### Finding Description
The broken invariant mirrors the report's core issue - the modifier `paidOnly()`/`paymentProvided()` used `>=` instead of `==`, letting excess unchecked value through and being silently accepted/consumed. Here, `updateRemoteUrl` uses an analogous loose equality: [2](#0-1) 

`protocolsMatch` only compares `URL.parse(...).protocol` (i.e. literally the string `"https:"`), not the hostname, owner, or repo name of the new URL. `remoteUrlUnchanged` only asserts that the *old* remote still equals the *previously known* API value (guarding against a user who manually reconfigured their remote) - it does not validate the *new* value at all. As long as those two conditions hold and the new clone URL differs from the current remote (`!urlsMatch`), the code trusts the `clone_url` field from the `IAPIRepository` object completely and calls:

```ts
await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
```

This is a direct analog of the "modifier that should be `==` is `>=`" bug class: the check that gates a state-changing write only validates a coarse, easily-satisfiable property (`protocol` string equality) rather than the object identity that actually matters (hostname/owner/repo). `IAPIRepository.clone_url` originates from a GitHub API response - one of the explicitly allowed attacker-controlled inputs ("a GitHub API object"). A malicious or compromised GitHub Enterprise server (or any server the app account is configured against) can return an arbitrary `clone_url` with the same scheme, and Desktop will silently rewrite `origin` to point there.

### Impact Explanation
Once the remote is silently repointed, all future `git fetch`/`git pull`/`git push` operations target the attacker's server without any user-visible diff or confirmation dialog. This satisfies "silent corruption of what the user commits or pushes": pushes intended for the legitimate repository are instead delivered to (and can be captured/altered by) the attacker's server, and git's credential helper will present the user's stored GitHub credentials/token to that new host during authentication, enabling credential exfiltration.

### Likelihood Explanation
Exploitation requires the user's Desktop to be pointed at (or fetching metadata from) an API endpoint under attacker influence - e.g. a malicious/compromised GitHub Enterprise instance, or a man-in-the-middle on the API channel for an account added to Desktop. This is a realistic "attacker controls a GitHub API object" scenario per the task's valid-impact criteria, and requires no local access, no admin rights, and no pre-existing malware; it happens automatically during a routine repository/account refresh cycle.

### Recommendation
Do not trust `clone_url` from the API response for automatic remote rewriting unless the hostname (and ideally owner/name) also match the account's known/trusted endpoint. At minimum, compare full parsed `hostname` (via `parseRemote`) in addition to protocol before calling `setRemoteURL`, and/or surface a user-facing confirmation before silently changing `origin`.

### Proof of Concept
1. Add a GitHub Enterprise account in Desktop pointing at `https://ghe.evil-or-compromised.example`.
2. Clone/open a repository tracked under that account; Desktop caches `gitHubRepository.cloneURL`.
3. The compromised/malicious API endpoint later returns the same repository's metadata but with `clone_url: "https://attacker.example.com/owner/repo.git"` (same `https:` protocol).
4. During the next background repository refresh, `updateRemoteUrl` evaluates `protocolsMatch` (true, both `https:`), `remoteUrlUnchanged` (true, local remote still equals previously cached clone URL), and `!urlsMatch` (true, since the URL changed) — triggering `gitStore.setRemoteURL('origin', 'https://attacker.example.com/owner/repo.git')` with no user prompt.
5. Subsequent `git push` from Desktop now silently targets `attacker.example.com`, potentially exposing the pushed commits and the user's stored credentials. [3](#0-2)

### Citations

**File:** app/src/lib/stores/updates/update-remote-url.ts (L1-47)
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

**File:** app/test/unit/stores/updates/update-remote-url-test.ts (L96-129)
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
})
```
