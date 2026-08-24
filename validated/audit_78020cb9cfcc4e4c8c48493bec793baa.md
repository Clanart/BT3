### Title
Remote URL from GitHub API (`clone_url`) is auto-applied without protocol/format validation, enabling arbitrary command execution via git transport helpers - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
The Sherlock finding is a broken-invariant "unbounded value acceptance" bug: a setter takes attacker-influenced input with no validity/range check, and that unchecked value is later trusted for a sensitive operation. The Desktop analog is `updateRemoteUrl`, which copies `apiRepo.clone_url` — a value returned by the GitHub API for a repository the current user may not control (e.g. a fork whose upstream/parent metadata is attacker-influenced) — directly into the local repository's git remote configuration via `gitStore.setRemoteURL`, with no validation of scheme/format beyond a loose protocol-match heuristic.

### Finding Description
`updateRemoteUrl` computes whether to overwrite the local remote URL using only three checks: whether the "protocol" strings parsed by `URL.parse` match, whether the previous remote matched the previously known `cloneURL`, and whether the new url differs from the current remote. [1](#0-0) 

None of these checks constrain the *shape* of the accepted value to a safe git transport (`https://`, `git://`, `ssh://`, `git@host:...`). `URL.parse` only extracts whatever it can and same-protocol comparison is easily satisfied — e.g. two `ext::`-style or otherwise unusual URLs, or a URL whose "protocol" segment happens to coincide, will pass the check unnoticed since the parser silently returns `null` for the protocol on many non-standard/malformed schemes and the code treats that as "assume SSH, don't touch."

The value is then written straight to git via `setRemoteURL`, which itself performs no allow-listing of the URL: [2](#0-1) 

The same missing validation exists in `addRemote`: [3](#0-2) 

git itself supports the `ext::` remote helper transport, which executes an arbitrary shell command as part of any fetch/push/clone against that remote (this is a well-known git feature, not an Desktop bug in itself). Nowhere in the environment/execution path is `GIT_ALLOW_PROTOCOL` (or an equivalent allow-list) set to restrict which transport helpers git is permitted to invoke: [4](#0-3) 

Compare this to `app/src/lib/git/clone.ts`, which the maintainers clearly hardened against a related class of "unvalidated externally-derived value" bugs (path traversal via crafted clone URLs), adding `isClonePathSensitive`, `sanitizeCloneName`, and dedicated regression tests: [5](#0-4) [6](#0-5) 

No equivalent protocol/scheme allow-list guard exists for `addRemote`/`setRemoteURL`/`updateRemoteUrl`, so a value sourced from the GitHub API (`clone_url`) — which is attacker-influenced whenever the user is looking at, forking, or contributing to a repository controlled by an attacker — can silently become the trusted "default remote" used for all future fetch/push/pull operations.

### Impact Explanation
If `apiRepo.clone_url` can be made to resolve to a git "smart" or `ext::`-style transport target, silently swapping it in as the repository's default remote means the *next* fetch, pull, or push performed by Desktop (which happens automatically/frequently, e.g. background fetch) will invoke that transport with attacker-chosen parameters, without a further explicit user confirmation of the new URL value — the change happens transparently in `updateRemoteUrl`. This is exactly the "fee setter" pattern from the report: a value is accepted from an untrusted-but-plausible source (there, `msg.value`/fee parameter set by owner; here, `clone_url` returned for a repository object) with no range/scheme validation, and later used to silently affect an operation the victim did not explicitly re-consent to (there, draining excess value; here, redirecting/hijacking network operations against attacker infrastructure).

### Likelihood Explanation
Requires that a user has added/authenticated a repository whose GitHub API repository object (fork, renamed repo, or similar) can return a `clone_url` differing from what git considers a "matching" host/owner/name under the loose `urlMatchesRemote`/protocol-string checks — i.e., no local access, no admin rights, and no prior malware needed; the trigger is simply the normal Desktop workflow of refreshing repository metadata for a repository the attacker controls or has influenced (e.g. a fork the victim is contributing to). The remaining question of exact exploitability (whether GitHub's API can be coaxed into returning a non-standard `clone_url`, and whether git's `ext::` helper is reachable without an explicit `protocol.ext.allow` opt-in in the user's git config) could not be fully confirmed from the index alone — some `.gitconfig`/git-version-specific protections (`protocol.ext.allow=never` is git's own default deny since Git 2.x) may already mitigate the most severe RCE variant. Given that, this should be treated as a validation gap warranting a defense-in-depth fix rather than a confirmed end-to-end RCE.

### Recommendation
- Restrict `setRemoteURL`/`addRemote`/`updateRemoteUrl` to only accept URLs matching the same protocol allow-list Desktop already trusts for cloning (`https`, `ssh`, `git`), rejecting anything that doesn't parse via `parseRemote`/a stricter scheme check.
- Set `GIT_ALLOW_PROTOCOL` in `envForRemoteOperation` to an explicit allow-list (`http:https:ssh:git`) for every git invocation that touches a remote, closing off `ext::`/`fd::`/other helper transports regardless of how the URL entered the system.
- In `updateRemoteUrl`, don't silently rewrite the remote when the new `clone_url` fails strict validation — surface it to the user for explicit confirmation instead of applying it transparently.

### Proof of Concept
1. Attacker controls, or gets a victim to interact with (e.g. via a fork/contribution flow), a GitHub repository whose API-reported `clone_url` is not a standard `https`/`ssh` URL recognized by `parseRemote`, but still parses a "protocol" segment equal to the victim's current remote's protocol via `URL.parse`.
2. Victim's Desktop periodically calls `updateRemoteUrl` (`app/src/lib/stores/updates/update-remote-url.ts:7-45`) while refreshing repository metadata; `protocolsMatch && remoteUrlUnchanged && !urlsMatch` evaluates true, and `gitStore.setRemoteURL` rewrites the local `origin` (or default) remote to the attacker-supplied URL without further prompting.
3. On the next background fetch/push (triggered automatically by Desktop), git invokes the now-attacker-controlled transport target; absent a `GIT_ALLOW_PROTOCOL` restriction in `envForRemoteOperation` (`app/src/lib/git/environment.ts:76-81`), any transport helper git is willing to run (subject to the victim's own git version/config defaults) executes against attacker-chosen input.

### Citations

**File:** app/src/lib/stores/updates/update-remote-url.ts (L18-44)
```typescript
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

**File:** app/src/lib/git/environment.ts (L76-81)
```typescript
export async function envForRemoteOperation(remoteUrl: string) {
  return {
    ...envForAuthentication(),
    ...(await envForProxy(remoteUrl)),
  }
}
```

**File:** app/src/lib/git/clone.ts (L10-47)
```typescript
/**
 * Check whether a resolved clone path targets a sensitive location that
 * should never be used as a clone destination. This is a backstop against
 * path traversal attacks where a crafted URL tricks the UI into deriving
 * a clone path outside the intended base directory.
 */
function isClonePathSensitive(unresolvedClonePath: string): boolean {
  const clonePath = Path.resolve(unresolvedClonePath).toLowerCase()
  const home = Path.resolve(homedir()).toLowerCase()

  if (clonePath === home) {
    return true
  }

  const sensitiveLocations = [
    Path.join(home, '.ssh'),
    Path.join(home, '.gnupg'),
    Path.join(home, '.config'),
    Path.join(home, '.config', 'git'),
    Path.join(home, '.gitconfig'),
  ]

  if (__WIN32__) {
    const appData = process.env.APPDATA
    if (appData) {
      sensitiveLocations.push(appData.toLowerCase())
      sensitiveLocations.push(Path.join(appData, 'gnupg').toLowerCase())
    }
  }

  for (const sensitive of sensitiveLocations) {
    if (clonePath === sensitive || clonePath.startsWith(sensitive + Path.sep)) {
      return true
    }
  }

  return false
}
```

**File:** app/test/unit/clone-path-safety-test.ts (L1-27)
```typescript
import { describe, it } from 'node:test'
import assert from 'node:assert'
import * as Path from 'path'
import {
  parseRepositoryIdentifier,
  sanitizeCloneName,
} from '../../src/lib/remote-parsing'

describe('sanitizeCloneName', () => {
  it('returns a simple name unchanged', () => {
    assert.equal(sanitizeCloneName('Hello-World'), 'Hello-World')
  })

  it('extracts last component from backslash-separated traversal', () => {
    assert.equal(sanitizeCloneName('x..\\..\\..\\..\\foo'), 'foo')
  })

  it('rejects names that resolve to .. or empty', () => {
    assert.equal(sanitizeCloneName('..'), null)
    assert.equal(sanitizeCloneName(''), null)
    assert.equal(sanitizeCloneName('.git'), null)
  })

  it('does not traverse from default basepath (#x\\..\\..\\..\\.ssh)', () => {
    assert.equal(sanitizeCloneName('x..\\..\\..\\../.ssh'), '.ssh')
  })
})
```
