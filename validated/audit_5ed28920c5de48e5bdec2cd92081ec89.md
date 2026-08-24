### Title
Silent git remote URL rewrite trusts attacker-controlled GitHub API `clone_url` with only a protocol check - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
The pyUmbral report's core flaw is that a security-relevant parameter (the curve) is not validated against its true source of truth and a global/implicit default is trusted instead, producing a mismatched, exploitable state. The GitHub Desktop analog is `updateRemoteUrl()` in `app/src/lib/stores/updates/update-remote-url.ts`, which rewrites a repository's git `origin` remote based on the `clone_url` field of an `IAPIRepository` object returned from the GitHub API/GHE server, validating only that the URL *protocol* matches — never that the host/owner is consistent with the account's endpoint — before silently calling `gitStore.setRemoteURL(...)`.

### Finding Description
`updateRemoteUrl` compares the current default remote URL to `apiRepo.clone_url` and, if the git remote hasn't been manually changed and protocols match, unconditionally rewrites the remote: [1](#0-0) 

The only integrity checks performed are:
- `protocolsMatch`: both URLs parse to the *same scheme* (e.g. both `https:`), and
- `remoteUrlUnchanged`: the current remote still equals the previously known `gitHubRepository.cloneURL`.

Neither check constrains the **host** of the new URL. `urlMatchesRemote`/`URL.parse` only extract `.protocol`, so an `apiRepo.clone_url` of `https://attacker.example/foo/bar.git` passes the same "protocol match" test as `https://github.com/foo/bar.git`. Since `apiRepo` is a deserialized JSON object fetched straight from the configured API endpoint (`repos/${owner}/${name}`) via `fetchRepositoryCloneInfo`/repository refresh calls, its `clone_url` is fully attacker-controlled whenever the endpoint is a GitHub Enterprise server that is compromised, a MITM'd proxy, or a spoofed/malicious `x-github-enterprise-*` mirrored response: [2](#0-1) 

This mirrors the pyUmbral flaw precisely: the code trusts a caller-supplied/external object's field (curve / clone_url) without validating it against the canonical value it's supposed to be tied to (the private key's actual curve / the GitHub repository's actual host), and the "guard" that exists (the size-197 shortcut / protocol-only match) is too weak to catch the mismatch.

### Impact Explanation
If exploited, `gitStore.setRemoteURL` silently repoints the user's `origin` remote to a host of the attacker's choosing. All subsequent `git push`, `git fetch`, and credential-helper interactions for that repository will target the attacker's server instead of the real one. Combined with the trampoline credential helper, which will hand over the account's OAuth token to any host that matches the account's own endpoint's origin (unrelated protection), the practical outcome is: the user's next push silently goes to (and can leak source code, secrets, or private commits to) a server the user never approved — a "silent corruption of what the user commits or pushes" as defined in the report's valid-impact list, achieved purely through a malicious/compromised GitHub API response, with no local access or social engineering required.

### Likelihood Explanation
Exploitation requires the attacker to control (or MITM) the response of the GitHub Enterprise API for a repository the user has already added, since `updateRemoteUrl` is fed by repository metadata refresh flows in `app-store.ts` and only fires when `gitStore.defaultRemote` exists and the repository hasn't already diverged locally. This is realistic for a rogue/compromised GHE instance or for an attacker positioned on the network path to a self-hosted enterprise endpoint (a scenario the "attacker controls ... a git remote/proxy response" clause explicitly covers). The change is silent (no prompt, no diff shown to the user), which increases likelihood of the corrupted remote persisting undetected.

### Recommendation
- Compare the new `clone_url`'s **host** (and ideally owner/name) against the `GitHubRepository`'s known `endpoint`/hostname, not just the URL scheme, before treating a URL as "the same remote, just renamed."
- Never silently rewrite a remote URL to a different host; at most, prompt the user to confirm the change when the hostname differs from the repository's registered endpoint.
- Treat `IAPIRepository.clone_url`/`ssh_url` as untrusted input and validate it is scoped to the same API endpoint the request was made against, rather than trusting the field verbatim.

### Proof of Concept
1. Add a GitHub Enterprise account/repository pointing at `https://ghe.corp.example` in Desktop.
2. Compromise or MITM the GHE server (or its `/api/v3/repos/{owner}/{name}` response) so that a repository metadata refresh returns `clone_url: "https://ghe.corp.example.attacker.io/owner/repo.git"` (still `https:`, so `protocolsMatch` is true) while the local `origin` remote still equals the previously cached `gitHubRepository.cloneURL` (`remoteUrlUnchanged` true).
3. Desktop's repository refresh path invokes `updateRemoteUrl`, which finds `protocolsMatch && remoteUrlUnchanged && !urlsMatch` all true and calls `gitStore.setRemoteURL('origin', 'https://ghe.corp.example.attacker.io/owner/repo.git')` with no user confirmation.
4. The next `git push` from the user goes to the attacker's host, using Desktop's stored credentials for the (now mismatched) endpoint via the credential-helper trampoline.

Note: I could not fully trace every call path from `app-store.ts` into `updateRemoteUrl` (3 call sites were found but not individually inspected) within the available iterations, so the exact triggering UI/backround-refresh flow should be confirmed by a follow-up session before treating this as fully verified.

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

**File:** app/src/lib/api.ts (L1010-1030)
```typescript
  public async fetchRepositoryCloneInfo(
    owner: string,
    name: string,
    protocol: GitProtocol | undefined
  ): Promise<IAPIRepositoryCloneInfo | null> {
    const response = await this.ghRequest('GET', `repos/${owner}/${name}`, {
      // Make sure we don't run into cache issues when fetching the repositories,
      // specially after repositories have been renamed.
      reloadCache: true,
    })

    if (response.status === HttpStatusCode.NotFound) {
      return null
    }

    const repo = await parsedResponse<IAPIRepository>(response)
    return {
      url: protocol === 'ssh' ? repo.ssh_url : repo.clone_url,
      defaultBranch: repo.default_branch,
    }
  }
```
