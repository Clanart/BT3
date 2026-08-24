## Analysis

The Palmera report's broken invariant is: **a primitive meant for trusted use is exposed to attacker-controlled input without the safety check that upstream tooling already provides**, letting attacker-supplied content (the `to`/delegatecall target) execute with the victim's full privileges, ultimately destroying the target.

The GitHub Desktop analog with the clearest local-code evidence is in `app/src/lib/git/clone.ts`, where Desktop explicitly disables Git's own built‑in clone protection while performing a `--recursive` clone of an attacker-controlled remote URL. [1](#0-0) 

`GIT_CLONE_PROTECTION_ACTIVE` is an internal Git safety flag related to Git's defenses (post CVE‑2024‑32004 class issues) against malicious repository/submodule content abusing the recursive clone/checkout path (e.g. crafted repositories that place executable hook-like content that Git's protections are designed to block from running during clone). Desktop forces this flag to `'false'` unconditionally for every clone, on every `git clone --recursive` invocation of a user-supplied/GitHub-served URL. [2](#0-1) 

Everything else in this same file, and in sibling files, is defensive hardening for exactly this bug class — sensitive-destination checks, path-traversal-safe clone naming, and `resolveWithin` symlink-escape checks — which makes the explicit disabling of Git's native clone protection stand out as the anomaly rather than an intentional design choice. [3](#0-2) [4](#0-3) [5](#0-4) 

This satisfies the "Valid Impact" bar: the attacker controls a cloned/fetched repository (a normal, unprivileged action a Desktop user takes — cloning any URL, including ones from search results, phishing links, or malicious forks), and the result is potential code execution or file writes outside the intended repository directory via a path Git itself would otherwise refuse.

### Title
Git's native clone protection is force-disabled during `git clone --recursive`, re-opening malicious-repository code-execution/file-write path - (File: app/src/lib/git/clone.ts)

### Summary
`clone()` in `app/src/lib/git/clone.ts` unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the environment used for every `git clone --recursive` invocation, regardless of the remote URL's trust level. This deliberately turns off a Git-provided safety guard whose purpose is to prevent a malicious repository (including its recursively-cloned submodules) from tricking Git into unsafe filesystem operations or hook-like execution during clone/checkout. Combined with `--recursive`, any attacker who controls the content of a repository (or one of its submodules) that a Desktop user clones can rely on this guard being off in Desktop, even though it would normally be on when the user runs `git clone` from a terminal with a modern Git.

### Finding Description
`clone()` builds its execution environment by merging `envForRemoteOperation(url)` with a hardcoded override:
```
const env = {
  ...(await envForRemoteOperation(url)),
  GIT_CLONE_PROTECTION_ACTIVE: 'false',
}
``` [6](#0-5) 

then runs `git clone --recursive -- <url> <path>` with this environment. [7](#0-6) 

`GIT_CLONE_PROTECTION_ACTIVE` is a Git-internal flag used by newer Git versions to keep protections active for the duration of clone/submodule-init/checkout, so that content pulled from an untrusted, attacker-supplied remote cannot smuggle in dangerous filesystem structures (symlinked/hardlinked paths, embedded `.git` directories, or content designed to be treated specially during checkout) that Git's own hardening is meant to reject. By explicitly forcing this value to `'false'` for every clone, Desktop actively undoes that guard rather than merely omitting it — meaning a locally-installed, patched Git binary that would refuse or sanitize such an attack when invoked normally from a shell is coerced by Desktop into skipping that check.

This is the direct structural analog of the Palmera bug: a function that is supposed to operate on behalf of the caller (`execTransactionOnBehalf` → `clone`) forwards attacker-influenced/attacker-owned content (`to`/delegatecall target → cloned repository + its submodules) through a low-level execution primitive (`execute` → `git clone --recursive`) while a guard that should stop unsafe execution against untrusted content (role/operation check → `GIT_CLONE_PROTECTION_ACTIVE`) is bypassed/disabled by the caller itself.

### Impact Explanation
If an attacker crafts a repository (or a submodule referenced by it) exploiting the specific clone/checkout weaknesses that `GIT_CLONE_PROTECTION_ACTIVE` is meant to close, a victim who clones that URL in GitHub Desktop — a completely ordinary, unprivileged action — could have Desktop perform filesystem writes or code paths outside the intended repository boundary that an unmodified `git clone --recursive` invocation (with the protection left on) would have refused. Depending on the exact protected scenario in the installed Git version, this can range from writing files outside the clone destination to executing attacker-controlled content during the recursive submodule checkout, i.e. code execution, matching the report's "High" impact class (destructive/irreversible action triggered by an unprivileged, attacker-influenced input).

### Likelihood Explanation
Every clone performed through Desktop's `clone()` function is affected, with no per-URL trust distinction — HTTPS/SSH clone from the clone dialog, "Clone Again" from a missing repository, and any programmatic clone path all funnel through this same function. [8](#0-7) 
The only prerequisite is that the victim clones a URL supplied or influenced by the attacker (a link, a forked/malicious repo, or a repo with a malicious submodule reference) — this requires no local access, no admin rights, and no prior compromise, satisfying the "Valid Impact" criteria (attacker controls a cloned/fetched repository).

### Recommendation
Remove the hardcoded override, or at minimum only set `GIT_CLONE_PROTECTION_ACTIVE` to `'false'` when it is verifiably safe to do so (e.g. only for known-trusted, already-verified GitHub-hosted URLs, and never for arbitrary/user-entered remotes or `--recursive` submodule fetches). Prefer leaving Git's own protection enabled by default and only opt out with an explicit, reviewed justification tied to a specific compatibility requirement, with a comment documenting why disabling a security control is safe in that narrow case.

### Proof of Concept
1. Prepare an attacker-controlled Git repository containing a submodule (or a `.gitmodules`/blob layout) crafted to exploit the specific unsafe-clone/checkout condition that `GIT_CLONE_PROTECTION_ACTIVE` is designed to prevent in the victim's installed Git version.
2. Share the clone URL with the victim (e.g., via a link, README instructions, or a forked project) and have them use GitHub Desktop's "Clone repository" or "Clone Again" flow.
3. Desktop calls `clone(url, path, options)`, which executes `git -c init.defaultBranch=... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE` forced to `'false'` in the environment. [1](#0-0) 
4. Because the protection is disabled for the whole operation, the crafted repository/submodule is processed without the guard that an unmodified `git clone --recursive` (run directly from a shell without this override) would have applied, allowing the attacker's crafted content to reach the unsafe code path.

### Citations

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

**File:** app/src/lib/git/clone.ts (L68-125)
```typescript
export async function clone(
  url: string,
  path: string,
  options: CloneOptions,
  progressCallback?: (progress: ICloneProgress) => void
): Promise<void> {
  if (isClonePathSensitive(path)) {
    throw new Error(
      `The clone destination "${path}" targets a sensitive system location. ` +
        'Cloning into this directory is not allowed.'
    )
  }

  const env = {
    ...(await envForRemoteOperation(url)),
    GIT_CLONE_PROTECTION_ACTIVE: 'false',
  }

  const defaultBranch = options.defaultBranch ?? (await getDefaultBranch())

  const args = [
    '-c',
    `init.defaultBranch=${defaultBranch}`,
    'clone',
    '--recursive',
  ]

  let opts: IGitStringExecutionOptions = { env }

  if (progressCallback) {
    args.push('--progress')

    const title = `Cloning into ${path}`
    const kind = 'clone'

    opts = await executionOptionsWithProgress(
      { ...opts, trackLFSProgress: true },
      new CloneProgressParser(),
      progress => {
        const description =
          progress.kind === 'progress' ? progress.details.text : progress.text
        const value = progress.percent

        progressCallback({ kind, title, description, value })
      }
    )

    // Initial progress
    progressCallback({ kind, title, value: 0 })
  }

  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
```

**File:** app/src/lib/remote-parsing.ts (L72-116)
```typescript
/**
 * Extracts a safe single-component directory name from a URL-derived repo name.
 *
 * Mirrors the approach of git's `git_url_basename()` in `dir.c`: treat `/`,
 * `\`, and `:` as path separators, take the last non-empty component, strip a
 * trailing `.git` suffix, and reject traversal segments. This ensures the
 * result is always a single path component that cannot escape the parent
 * directory when passed to `Path.join()`.
 *
 * Examples:
 *  - `"Hello-World"` → `"Hello-World"` (unchanged)
 *  - `"desktop.git/../../otherdir"` → `"otherdir"` (last component, traversal segments skipped)
 *  - `".."` → `null` (traversal-only name rejected)
 *
 * See: https://github.com/git/git/blob/master/dir.c (`git_url_basename`)
 */
export function sanitizeCloneName(name: string): string | null {
  const components = name.split(/[/\\:]/)

  let lastComponent = ''
  for (let i = components.length - 1; i >= 0; i--) {
    if (components[i].length > 0) {
      lastComponent = components[i]
      break
    }
  }

  if (lastComponent.length === 0) {
    return null
  }

  if (lastComponent.endsWith('.git')) {
    lastComponent = lastComponent.slice(0, -4)
  }

  if (
    lastComponent === '..' ||
    lastComponent === '.' ||
    lastComponent.length === 0
  ) {
    return null
  }

  return lastComponent
}
```

**File:** app/src/lib/path.ts (L36-72)
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
}
```

**File:** app/src/ui/missing-repository.tsx (L169-188)
```typescript
  private cloneAgain = async () => {
    const gitHubRepository = this.props.repository.gitHubRepository
    if (!gitHubRepository) {
      return
    }

    const cloneURL = gitHubRepository.cloneURL
    if (!cloneURL) {
      return
    }

    try {
      await this.props.dispatcher.cloneAgain(
        cloneURL,
        this.props.repository.path
      )
    } catch (error) {
      this.props.dispatcher.postError(error)
    }
  }
```
