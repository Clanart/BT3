No vulnerability found for this question.

**Analysis:**

The described asymmetry is not a security defect. `hostname.toLowerCase()` at [1](#0-0)  only normalizes the protocol-routing token (`oauth` vs `openrepo`), which is standard practice since URL hostnames are inherently case-insensitive per RFC. The `pathName`/`parsedPath` at [2](#0-1)  correctly preserve case because they represent the actual repository URL and ref data, which are case-sensitive — this is the intended and correct behavior, not a bug.

The claim that this leads to "case-based path or ref confusion" doesn't hold up when tracing the sinks:

1. **The `url` field** is only used for repository lookup/matching via `urlsMatch` in `doesRepositoryMatchUrl` [3](#0-2)  or passed to clone/branch logic. A case mismatch here at worst causes Desktop to treat two differently-cased URLs as distinct repos (re-cloning), which is not a security boundary — no privileged data or credential is at stake.

2. **The `filepath` field**, which is the actual filesystem-touching sink, is validated with `isAbsolute()` and then resolved via `resolveWithin`, which normalizes the path, resolves it with `realpath`, and verifies the resolved real path is contained under the repository root via `startsWith` [4](#0-3) , [5](#0-4) . This containment check operates on resolved absolute paths regardless of case; there's no case-insensitive comparison in Desktop's logic that an attacker could exploit to bypass it. Any case-insensitivity would come from the underlying OS filesystem (Windows/macOS default), which is a platform property, not something Desktop's code introduces or fails to guard — the `realpath`-based check still enforces containment.

3. **The `branch` field** is validated by `testForInvalidChars` regardless of case [6](#0-5) , and branch checkout ultimately goes through git itself, which enforces its own ref resolution rules.

There is no case-insensitive equality check anywhere in this flow that compares attacker-controlled values to a security-relevant value (e.g., an allowlist, origin check, or path boundary) in a way that a differently-cased `parsedPath`/`filepath` could defeat. The proof-of-concept idea (diffing case-varied `openrepo`/`OpenRepo`/`OPENREPO` deep links) would show identical routing (expected, since `openrepo` is just an action name) and identical downstream path-containment behavior (enforced by `resolveWithin`'s `realpath` check, not string case comparison).

### Citations

**File:** app/src/lib/parse-app-url.ts (L76-76)
```typescript
  const actionName = hostname.toLowerCase()
```

**File:** app/src/lib/parse-app-url.ts (L90-96)
```typescript
  const pathName = parsedURL.pathname
  if (!pathName || pathName.length <= 1) {
    return unknown
  }

  // Trim the trailing / from the URL
  const parsedPath = pathName.substring(1)
```

**File:** app/src/lib/parse-app-url.ts (L114-116)
```typescript
    if (branch != null && testForInvalidChars(branch)) {
      return unknown
    }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1920-1938)
```typescript
  private doesRepositoryMatchUrl(
    repo: Repository | CloningRepository,
    url: string
  ): repo is RepositoryWithGitHubRepository {
    if (repo instanceof Repository && isRepositoryWithGitHubRepository(repo)) {
      const originRepoUrl = repo.gitHubRepository.htmlURL
      const upstreamRepoUrl = repo.gitHubRepository.parent?.htmlURL ?? null

      if (originRepoUrl !== null && urlsMatch(originRepoUrl, url)) {
        return true
      }

      if (upstreamRepoUrl !== null && urlsMatch(upstreamRepoUrl, url)) {
        return true
      }
    }

    return false
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1972)
```typescript
    if (filepath !== null) {
      if (isAbsolute(filepath)) {
        log.error(`Refusing to open absolute path: ${filepath}`)
        return
      }

      const resolved = await resolveWithin(repository.path, filepath)

      if (resolved !== null) {
        shell.showItemInFolder(resolved)
      } else {
        log.error(
          `Prevented attempt to open path outside of the repository root: ${filepath}`
        )
      }
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
