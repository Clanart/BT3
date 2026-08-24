Based on my investigation, I found a concrete analog. The seed report's broken invariant is "attacker-controlled parameter passed unsanitized into a sensitive operation" (there, an unchecked amount/path parameter fed to a swap call). The closest verified Desktop analog is **argument injection in `git remote add`/`git remote set-url` via an attacker-controlled fork clone URL**, which lacks the `--end-of-options` (`--`) separator that the `clone()` path already uses.

### Title
Argument injection via attacker-controlled fork `cloneUrl` in `addRemote`/`setRemoteURL` (missing `--` separator) - ([File: app/src/lib/git/remote.ts])

### Summary
When Desktop clones/fetches for a fork-based pull request, it takes the `headCloneUrl` value from the GitHub API (PR head repo `clone_url`) and passes it straight to `addRemote()`, which builds the git invocation as `['remote', 'add', name, url]` with no `--` separator before `url`. [1](#0-0) 
Contrast this with `clone()`, which explicitly appends `'--'` before the URL as a hardening measure against exactly this class of issue: [2](#0-1) 
`setRemoteURL` has the same gap. [3](#0-2) 

### Finding Description
`_findPullRequestBranch` resolves the remote for a PR by calling `addRemote(repository, forkRemoteName, headCloneUrl)`, where `headCloneUrl` originates from the pull request's head repository metadata — a value fully controlled by whoever forks and opens the PR, not by the repository owner. [4](#0-3) 
This mirrors the seed report's broken invariant: an external, attacker-influenced value (there, swap parameters/path; here, a clone URL) is passed into a sensitive operation without being constrained to its expected form. `remote-parsing.ts`'s `parseRemote` regexes are used elsewhere for display/protocol detection but are not enforced as a gate before `addRemote`/`setRemoteURL` are invoked with API-sourced URLs. [5](#0-4) 

Because `url` is appended as a bare positional argument (not preceded by `--`), a value beginning with `-` is interpreted by git as an option rather than a literal URL, e.g. `--upload-pack=...`-style flags are the classic vector; `git remote add`/`set-url` have a narrower option surface than `git clone`/`git fetch`, so this does not directly hand the attacker `--upload-pack` RCE, but it is the same missing-guard pattern (`--` separator) that the authors clearly know is required, since they already apply it in `clone.ts`. The `resolveWithin` and `sanitizeCloneName` guards in `app/src/lib/path.ts` and `app/src/lib/remote-parsing.ts` protect path traversal for clone destinations, but they do not validate the *content* of a fork's `cloneUrl` before it reaches `addRemote`/`setRemoteURL`. [6](#0-5) [7](#0-6) 

### Impact Explanation
At minimum this allows a fork PR author to corrupt the victim's local remote configuration for their own repository (silently changing what "push"/"fetch" resolve to for that remote name) without the victim taking any unusual action beyond viewing/checking out the PR — matching the "silent corruption of what the user commits or pushes" impact category. It is not confirmed to yield direct RCE, since `git remote add`/`set-url` don't expose an equivalent to `clone`'s/`fetch`'s `--upload-pack`; this would need further verification against the exact git version's option parser to determine if any argument achieves file write or command execution.

### Likelihood Explanation
Moderate: exploitation requires the victim to interact with a pull request whose head repository URL is attacker-supplied, which is a normal, low-friction workflow in Desktop (viewing/checking out any fork PR triggers `_findPullRequestBranch`). No local access, credentials, or social engineering beyond "open this PR" is needed.

### Recommendation
Add the same `--` end-of-options separator used in `clone.ts` to the `addRemote` and `setRemoteURL` git invocations in `app/src/lib/git/remote.ts`, and/or validate that `headCloneUrl` (and any other API-sourced clone URL) matches one of the accepted protocol shapes in `remote-parsing.ts` (`parseRemote(url) !== null`) before it is used to add/set a remote.

### Proof of Concept
1. Fork a repository and open a pull request whose head repository's `clone_url` (as returned by the GitHub API) begins with `-` (e.g., a value crafted to be parsed as a git option instead of a URL) — this requires control over how the API surfaces the head clone URL for the fork, which is influenced by the fork's configuration.
2. In Desktop, open the PR from the base repository; this triggers `_findPullRequestBranch`, which calls `addRemote(repository, forkRemoteName, headCloneUrl)` with the crafted value as a bare positional argument. [4](#0-3) 
3. Because `remote.ts`'s `addRemote` omits the `--` separator present in `clone.ts`, git interprets the leading-`-` value as an option rather than a URL argument. [1](#0-0) 

**Caveat:** I could not fully verify a concrete end-to-end RCE payload for `git remote add`/`set-url` specifically (as opposed to `clone`/`fetch`, where `--upload-pack` injection is a documented technique) within the available index; the exact reachable git-option surface for `remote add`/`set-url` argument injection would need confirmation with the target git version to determine severity precisely.

### Citations

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

**File:** app/src/lib/git/clone.ts (L119-126)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
}
```

**File:** app/src/lib/stores/app-store.ts (L8646-8660)
```typescript

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

**File:** app/src/lib/remote-parsing.ts (L27-52)
```typescript
const remoteRegexes: ReadonlyArray<{ protocol: GitProtocol; regex: RegExp }> = [
  {
    protocol: 'https',
    regex: new RegExp(
      '^https?://(?:.+@)?(.+)/([^/]+)/([^/]+?)(?:/|\\.git/?)?$'
    ),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^git@(.+):([^/]+)/([^/]+?)(?:/|\\.git)?$'),
  },
  {
    protocol: 'ssh',
    regex: new RegExp(
      '^(?:.+)@(.+\\.ghe\\.com):([^/]+)/([^/]+?)(?:/|\\.git)?$'
    ),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^git:(.+)/([^/]+)/([^/]+?)(?:/|\\.git)?$'),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^ssh://git@(.+)/(.+)/(.+?)(?:/|\\.git)?$'),
  },
]
```

**File:** app/src/lib/path.ts (L36-71)
```typescript
async function _resolveWithin(
  rootPath: string,
  pathSegments: string[],
  options: {
    join: (...pathSegments: string[]) => string
    normalize: (p: string) => string
    resolve: (...pathSegments: string[]) => string
  } = Path
) {
  // An empty root path would let all relative
  // paths through.
  if (rootPath.length === 0) {
    return null
  }

  const { join, normalize, resolve } = options

  const normalizedRoot = normalize(rootPath)
  const normalizedRelative = normalize(join(...pathSegments))

  // Null bytes has no place in paths.
  if (
    normalizedRoot.indexOf('\0') !== -1 ||
    normalizedRelative.indexOf('\0') !== -1
  ) {
    return null
  }

  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
```

**File:** app/test/unit/clone-path-safety-test.ts (L9-27)
```typescript
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
