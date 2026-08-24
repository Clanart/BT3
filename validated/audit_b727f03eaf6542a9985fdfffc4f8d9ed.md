Based on my investigation, I found a concrete analog: **`parseAppURL`'s asymmetric validation between `branch` and `filepath` when parsing the `openRepo` deep-link action**, mirroring the ERC4626 pattern where one code path applies custom/guard logic and a structurally-parallel dependent path does not.

### Title
Unsanitized `filepath` parameter from `x-github-client://openrepo` deep links bypasses the same validation applied to `branch` - (File: `app/src/lib/parse-app-url.ts`)

### Summary
`parseAppURL` validates the `branch` query parameter from an incoming `openRepo`/`openrepo` deep link with `testForInvalidChars`, but performs **no validation at all** on the `filepath` parameter before returning it as part of `IOpenRepositoryFromURLAction`. [1](#0-0) 

### Finding Description
In `parseAppURL`, the `openrepo` action extracts three attacker-controlled query values — `pr`, `branch`, and `filepath` — from a URL that can originate from any link a user clicks (browser "Open in Desktop" flow, or a raw `x-github-client://` / `github-mac://` URL). [2](#0-1) 

`pr` is checked against `/^\d+$/`, and `branch` is checked with `testForInvalidChars(branch)`, both causing the whole action to be rejected as `unknown` if invalid: [3](#0-2) 

`filepath`, however, is read via `getQueryStringValue(query, 'filepath')` and passed straight through into the returned action object with zero sanitization: [4](#0-3) 

This is structurally the same class of bug as the ERC4626 report: a "custom"/guarded value (`branch`) has its own validation logic, while a semantically-parallel dependent value (`filepath`) that flows through the same object and downstream consumers does not inherit or receive equivalent guarding — exactly the "some dependent paths override/validate, others silently fall through to unguarded defaults" invariant break described in the report.

The `filepath` field is documented as "the file to open after cloning the repository" and is consumed downstream by the dispatcher/app-store handling of the `open-repository-from-url` action (referenced in `app/src/ui/dispatcher/dispatcher.ts`, 6 usages found) to open a file relative to the cloned repository path. [5](#0-4) 

I was not able to fully verify, within the indexed code available to me, the exact downstream code in `dispatcher.ts`/`app-store.ts` that joins `filepath` to the repository's working directory (index size limits prevented me from viewing all matches). The codebase does contain a dedicated safe-join primitive, `resolveWithin`/`resolveWithinPosix`/`resolveWithinWin32` in `app/src/lib/path.ts`, that is specifically designed to prevent path traversal and symlink escapes when combining an attacker-influenced relative path with a root directory — this is used elsewhere for conflict file paths. [6](#0-5) [7](#0-6) 

Unlike `buildConflictContext`, which explicitly guards file paths from git status output with `resolveWithin` before touching the filesystem, `parseAppURL`'s `filepath` has no equivalent traversal check (no `..` rejection, no null-byte check, no `resolveWithin` call) at the point where it is parsed and handed to the rest of the app.

### Impact Explanation
If any downstream consumer of `IOpenRepositoryFromURLAction.filepath` builds a path by naively joining it to the freshly cloned repository directory (e.g., to open the file in an editor or reveal it in the shell) without routing it through `resolveWithin`, an attacker who crafts a malicious "Open in Desktop" link (e.g., `x-github-client://openrepo/https://github.com/attacker/repo?filepath=../../../../.ssh/id_rsa`) could cause Desktop to open or reveal an arbitrary file outside the cloned repository once the victim clicks the link. This matches the "Valid Impact" criteria: the attacker controls a link the user clicks, and the potential result is file read/open outside the repo — parallel to the ERC4626 report's core problem of "safe" computed values (`convertToShares`/`convertToAssets`) not being consistently propagated to dependent operations, causing silent, unguarded behavior.

### Likelihood Explanation
Medium-to-low, contingent on unverified downstream code. The entry point itself (`parseAppURL`) is fully attacker-reachable via a single click with no additional user interaction required beyond the normal "Open in Desktop" flow, and the asymmetric validation (branch guarded, filepath unguarded) is confirmed in the source. However, I could not confirm from the indexed code whether the eventual filesystem operation that consumes `filepath` uses `resolveWithin` or an equivalent traversal guard — if it does, the practical exploitability is neutralized despite the parsing-layer gap.

### Recommendation
Apply the same class of guard used for conflict file paths (`resolveWithin`/`resolveWithinPosix` in `app/src/lib/path.ts`) to `filepath` before it is used to locate a file on disk, and/or reject `filepath` values containing path traversal sequences (`..`), absolute path prefixes, or null bytes at parse time in `parseAppURL`, consistent with how `branch` is already validated with `testForInvalidChars`.

### Proof of Concept
1. Attacker hosts or sends a link: `x-github-client://openrepo/https://github.com/attacker/public-repo?branch=main&filepath=..%2F..%2F..%2F..%2F.ssh%2Fid_rsa`
2. Victim (with Desktop's protocol handler registered) clicks the link.
3. `parseAppURL` returns `{ name: 'open-repository-from-url', url: 'https://github.com/attacker/public-repo', branch: 'main', filepath: '../../../../.ssh/id_rsa' }` — the `branch` field passed validation, and `filepath` was never checked. [1](#0-0) 
4. Desktop clones `attacker/public-repo` and, per its documented behavior of opening `filepath` "after cloning," attempts to open the resolved path — if the downstream handler in `dispatcher.ts`/`app-store.ts` does not sanitize this value (unconfirmed from available index), this results in file access outside the cloned repository directory.

**Confidence caveat:** I confirmed the parsing-layer inconsistency directly from source, but could not fully trace the sink in `app/src/ui/dispatcher/dispatcher.ts` due to index coverage limits on this query. A Devin session with full filesystem access would be needed to confirm whether the actual file-open call already sanitizes `filepath`, which would determine whether this is a live vulnerability or a defense-in-depth gap.

### Citations

**File:** app/src/lib/parse-app-url.ts (L22-23)
```typescript
  /** the file to open after cloning the repository */
  readonly filepath: string | null
```

**File:** app/src/lib/parse-app-url.ts (L98-124)
```typescript
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

**File:** app/src/lib/copilot-conflict-context.ts (L390-401)
```typescript
      // Guard against path traversal and symlink escapes (cross-platform)
      let absolutePath: string | null
      try {
        absolutePath = await resolveWithin(workingDirectory, file.path)
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path could not be resolved safely',
        }
      }
      if (absolutePath === null) {
```
