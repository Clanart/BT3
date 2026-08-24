## Summary

`updateRemoteUrl` in the Desktop codebase silently rewrites a repository's `origin` remote URL to whatever `clone_url` is returned by a `GitHubRepository`/API repository object, based only on a "did the user manually change it" heuristic — not on any confirmation that the new URL is still the location the user trusts. This mirrors the Abra NFT bug's invariant break: a value that was fixed and implicitly agreed to at trust-establishment time (the remote URL the user configured/cloned from) can later be swapped by data coming from an external, less-trusted source, and the code path that performs the swap never re-validates that the new value matches what the user originally consented to — it only checks that the *old* value hadn't drifted. [1](#0-0) 

### Title
Silent, unconfirmed rewrite of the trusted git `origin` remote URL from GitHub API repository data — (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Finding Description
`updateRemoteUrl(gitStore, gitHubRepository, apiRepo)` compares the repository's *current* remote URL to the `cloneURL` that was previously cached for the associated `GitHubRepository`, and to the *new* `clone_url` returned by a freshly-fetched API repository object: [2](#0-1) 

The only guards are:
1. `protocolsMatch` — the old and new URL use the same protocol.
2. `remoteUrlUnchanged` — the *previously cached* `gitHubRepository.cloneURL` still matches the local `defaultRemote.url` (i.e., the user hasn't manually customized their `origin`).

If both hold and `urlMatchesRemote(updatedRemoteUrl, gitStore.defaultRemote)` is false (the API's clone URL differs from the current one), Desktop calls `gitStore.setRemoteURL(...)` and rewrites `origin` to the new URL automatically, with **no user prompt and no re-validation that the new URL still points at a location the user actually intended to trust** — analogous to the oracle bug's missing `params.oracle == cur.oracle` check.

By contrast, the codebase itself demonstrates the "correct" pattern elsewhere: when an `upstream` remote's URL diverges from the expected parent, Desktop shows the `UpstreamAlreadyExists` dialog and requires explicit user confirmation before changing the remote: [3](#0-2) 

`updateRemoteUrl`, however, applies the equivalent operation to the primary `origin` remote non-interactively, based purely on trusting that the `GitHubRepository`/API object's `clone_url` field is authoritative.

### Impact Explanation
The corrupted value is the git `origin` remote URL — the destination Desktop uses for all subsequent `fetch`/`push`/`pull` operations for that repository. If the `clone_url` associated with the cached `GitHubRepository` object changes (e.g., because the repository the user is tracking is renamed/transferred, or a compromised/malicious collaborator with sufficient GitHub-side privileges alters repository metadata that flows into the cached `IAPIFullRepository`), Desktop will silently retarget the user's `origin` to the new URL without any confirmation dialog. Future pushes and fetches then go to a location the user never explicitly re-approved, which falls squarely under "silent corruption of what the user commits or pushes" in the accepted impact category.

### Likelihood Explanation
This code path fires automatically whenever the app refreshes GitHub repository metadata for a tracked repository (part of the app's periodic repository-info refresh flow feeding `updateRemoteUrl`), requiring no unusual user action — the user does not need to click anything for the remote rewrite to occur, only for Desktop to have previously associated the local repo with a `GitHubRepository` record. The precondition is that the upstream API-reported `clone_url` for that `GitHubRepository` changes in a way the user did not anticipate; I was unable to fully trace, within available tool budget, the exact call site in `app-store.ts` and the precise conditions (e.g., repository ID stability across a rename/transfer) under which an attacker with only repository-level privileges (not physical/local access) can cause that change to be reflected without the local `GitHubRepository` association itself being invalidated first — this part of the trust chain (how `dbID`/owner-name binding survives a rename/transfer) needs further verification via the full `app-store.ts` call site before treating this as fully confirmed rather than a strong structural analog.

### Recommendation
Require explicit user confirmation before silently rewriting the `origin`/default remote URL from API-sourced data, mirroring the existing `UpstreamAlreadyExists` pattern — i.e., surface a dialog showing the old and new URLs and let the user opt in, rather than auto-applying `setRemoteURL` whenever `remoteUrlUnchanged && protocolsMatch && !urlsMatch`.

### Proof of Concept
1. User clones a repository and Desktop associates it with a `GitHubRepository` record whose `cloneURL` matches the local `origin`.
2. On a subsequent repository-info refresh, the API-fetched `IAPIFullRepository.clone_url` for that same `GitHubRepository` differs from what's cached (e.g. due to a rename/transfer reflected upstream).
3. `updateRemoteUrl` finds `protocolsMatch === true` and `remoteUrlUnchanged === true` (user never touched `origin`) and `urlsMatch === false`, so it calls `gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)` automatically: [4](#0-3) 
4. The existing unit test confirms this exact automatic behavior: [5](#0-4) 
5. The user's next `git push`/`fetch` silently targets the new URL with no confirmation step, unlike the analogous `upstream` remote flow which does prompt the user.

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

**File:** app/src/ui/upstream-already-exists/upstream-already-exists.tsx (L60-76)
```typescript
            <li>
              Expected: <Ref>{replacementURL}</Ref>
            </li>
          </ul>
          <p>Would you like to update the remote to use the expected URL?</p>
        </DialogContent>
        <DialogFooter>
          <OkCancelButtonGroup
            destructive={true}
            okButtonText="Update"
            cancelButtonText="Ignore"
            onCancelButtonClick={this.onIgnore}
          />
        </DialogFooter>
      </Dialog>
    )
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
