## Finding: Git Clone Protection (CVE‑2024‑32002 mitigation) Explicitly Disabled During Recursive Clone

### Title
Disabled `GIT_CLONE_PROTECTION_ACTIVE` reintroduces malicious-submodule hook execution on `git clone --recursive` - (File: `app/src/lib/git/clone.ts`)

### Summary
The `clone()` helper always clones with `--recursive` and unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the environment passed to `git clone`, which disables Git's own built-in defense against maliciously nested/symlinked `.git` directories in submodules (the protection Git added for CVE‑2024‑32002). This mirrors the report's underlying bug class — a security-relevant check that is supposed to validate untrusted, attacker-supplied state (here, a hostile repository's submodule layout) is explicitly bypassed, letting the attacker-controlled object (the cloned repo) drive an unsafe outcome (arbitrary file write/hook execution) instead of being validated.

### Finding Description [1](#0-0) 

```
const env = {
  ...(await envForRemoteOperation(url)),
  GIT_CLONE_PROTECTION_ACTIVE: 'false',
}
...
const args = [
  '-c',
  `init.defaultBranch=${defaultBranch}`,
  'clone',
  '--recursive',
]
``` [2](#0-1) 

`GIT_CLONE_PROTECTION_ACTIVE` is the environment variable Git introduced alongside the CVE‑2024‑32002 fix to guard against repositories whose submodule paths collide with `.git` (via case-insensitivity or Unicode normalization on certain filesystems), which could otherwise let a hostile repo place executable hook files (e.g. `post-checkout`) into the superproject's `.git/hooks` and have Git auto-execute them during a recursive clone/checkout. By hard-coding this to `'false'`, Desktop deliberately turns that Git-side protection off for every clone it performs, regardless of the source of the URL.

The repository's other hardening — `isClonePathSensitive` [3](#0-2) , `sanitizeCloneName` [4](#0-3) , and `resolveWithin` [5](#0-4)  — only validate the *destination directory* that Desktop derives from the URL. None of them inspect the *contents* of the cloned tree (submodule paths/names), so they provide no protection against the specific hook-execution attack that `GIT_CLONE_PROTECTION_ACTIVE=false` reopens.

### Impact Explanation
An attacker who controls a repository that a victim clones through Desktop — via the "Clone repository" dialog, the `x-github-client://openRepo/...` protocol handler parsed in `parseAppURL` [6](#0-5)  and dispatched through `openOrCloneRepository`, or a CLI `--cli-clone` argument [7](#0-6)  — can craft a repository with a submodule whose path collides with `.git` on the victim's filesystem, containing a malicious hook. With Git's clone protection disabled, `git clone --recursive` will write and can execute that hook, achieving code execution outside of the intended repository sandbox on the victim's machine — the same "unprivileged, attacker-controlled clone object leads to code execution" impact class called out as valid in the report scope.

### Likelihood Explanation
Every code path that clones a repository in Desktop goes through this single `clone()` function, so the disabled protection applies universally, not just to a rare configuration. The "Open in Desktop"/deep-link and "Clone repository" flows are both normal, unprivileged, user-triggered actions requiring only that the victim clone an attacker-supplied URL — no local access, admin rights, or pre-existing compromise is needed, matching the report's required attacker model (attacker controls a cloned repository).

### Recommendation
Remove the explicit `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override (or set it to `'true'`/leave it unset so Git's default protection applies) unless there is a documented, narrowly-scoped reason recorded for disabling it. If a reason exists (e.g., compatibility with certain legitimate repos), gate the override behind an allowlist/explicit user confirmation instead of applying it unconditionally to all clones.

### Proof of Concept
1. Attacker publishes a repository containing a submodule entry whose configured path is chosen to collide with `.git` on a case-insensitive or Unicode-normalizing filesystem (the layout pattern addressed by CVE‑2024‑32002), with a crafted `post-checkout` (or similar) hook payload.
2. Victim opens `x-github-client://openRepo/<attacker-repo-url>` (or pastes the URL into "Clone repository") in GitHub Desktop.
3. Desktop calls `clone(url, path, options, ...)` in `app/src/lib/git/clone.ts`, which spawns `git -c init.defaultBranch=... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` in the environment.
4. Because Git's built-in protection is disabled, the hostile submodule layout is allowed to place its hook into the superproject's `.git/hooks`, and normal Git checkout/submodule-update behavior executes it — resulting in code execution on the victim's machine outside of the cloned repository's intended sandbox, without the victim taking any unusual action beyond cloning the attacker's URL.

### Citations

**File:** app/src/lib/git/clone.ts (L16-47)
```typescript
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

**File:** app/src/lib/git/clone.ts (L68-94)
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

```

**File:** app/src/lib/remote-parsing.ts (L72-88)
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

**File:** app/src/lib/parse-app-url.ts (L66-128)
```typescript
export function parseAppURL(url: string): URLActionType {
  const parsedURL = URL.parse(url, true)
  const hostname = parsedURL.hostname
  const unknown: IUnknownAction = { name: 'unknown', url }
  if (!hostname) {
    return unknown
  }

  const query = parsedURL.query

  const actionName = hostname.toLowerCase()
  if (actionName === 'oauth') {
    const code = getQueryStringValue(query, 'code')
    const state = getQueryStringValue(query, 'state')
    if (code != null && state != null) {
      return { name: 'oauth', code, state }
    } else {
      return unknown
    }
  }

  // we require something resembling a URL first
  // - bail out if it's not defined
  // - bail out if you only have `/`
  const pathName = parsedURL.pathname
  if (!pathName || pathName.length <= 1) {
    return unknown
  }

  // Trim the trailing / from the URL
  const parsedPath = pathName.substring(1)

  if (actionName === 'openrepo') {
    const pr = getQueryStringValue(query, 'pr')
    const branch = getQueryStringValue(query, 'branch')
    const filepath = getQueryStringValue(query, 'filepath')

    if (pr != null) {
      if (!/^\d+$/.test(pr)) {
        return unknown
      }

      // we also expect the branch for a forked PR to be a given ref format
      if (branch != null && !/^pr\/\d+$/.test(branch)) {
        return unknown
      }
    }

    if (branch != null && testForInvalidChars(branch)) {
      return unknown
    }

    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
    }
  }

  return unknown
}
```

**File:** app/src/main-process/main.ts (L282-291)
```typescript
  if (typeof args['cli-open'] === 'string') {
    handleCLIAction({ kind: 'open-repository', path: args['cli-open'] })
  } else if (typeof args['cli-clone'] === 'string') {
    handleCLIAction({
      kind: 'clone-url',
      url: args['cli-clone'],
      branch:
        typeof args['cli-branch'] === 'string' ? args['cli-branch'] : undefined,
    })
  }
```
