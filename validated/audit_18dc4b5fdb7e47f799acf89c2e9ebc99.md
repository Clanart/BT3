Based on the investigation, the strongest local analog to the original "attacker-controlled object triggers unguarded code in a privileged path" bug class is an **input-validation asymmetry in the `x-github-client://openRepo/...` deep-link handler**, where the `filepath` parameter is accepted without any of the sanitization applied to sibling parameters.

### Title
Unsanitized `filepath` parameter in "Open in Desktop" deep link allows path traversal outside cloned repository - (File: `app/src/lib/parse-app-url.ts`)

### Summary
`parseAppURL` in [1](#0-0)  parses the `x-github-client://openRepo/...` (and legacy `github-mac://openRepo/...`) deep link and extracts `pr`, `branch`, and `filepath` from the query string. `pr` is validated with a numeric regex, and `branch` is validated with `testForInvalidChars` from `sanitize-ref-name` [2](#0-1) , but `filepath` is taken from `getQueryStringValue(query, 'filepath')` and returned completely unvalidated [3](#0-2) . The unit tests confirm this asymmetry — `filepath` accepts arbitrary path-like content such as `Octokit.Reactive/Octokit.Reactive.csproj` with no path-traversal or invalid-character check applied [4](#0-3) .

### Finding Description
This is an unprivileged Desktop attack surface: a link the user clicks (`x-github-client://openRepo/<url>?filepath=...`) is handled by the OS-registered protocol handler and routed through `app.on('open-url', ...) → handleAppURL(url)` in the main process [5](#0-4) , ultimately producing an `IOpenRepositoryFromURLAction` that is dispatched via `dispatchURLAction` → `openRepositoryFromUrl(action)` in the renderer's dispatcher [6](#0-5) . The `filepath` field is documented as "the file to open after cloning the repository" [7](#0-6) , meaning it is later joined with the freshly cloned repository's local path to identify a file for the app to open (e.g., in the default editor or file-reveal action). Unlike `branch`, which is defended against invalid/traversal characters before being trusted, `filepath` receives no such check, so a value like `../../../../../../Users/victim/.ssh/id_rsa` or a Windows UNC-style path would pass through `parseAppURL` unchanged.

This mirrors the structural defect in the original report: an attacker-controlled value flows into a later trusted operation (there, an arbitrary contract address invoked via `supportsInterface`; here, an arbitrary path string later joined onto a trusted base directory) without a guard at the point where the value is consumed, even though nearby sibling fields in the same function *do* get validated — showing the omission is a gap rather than a deliberate design choice.

### Impact Explanation
If the consuming code (in `openRepositoryFromUrl`/the clone-then-open-file flow) joins `filepath` onto the local clone directory without resolving/containing the result (the same class of bug that `sanitizeCloneName` was explicitly introduced to fix for the repository name component, per `app/test/unit/clone-path-safety-test.ts`), a malicious "Open in Desktop" link could cause GitHub Desktop to open/read a file located outside the newly cloned repository directory — e.g., exfiltrating or displaying sensitive local files by tricking the app into opening them as if they were part of the clone. This falls squarely in the valid-impact category: attacker controls a deep link the user clicks, resulting in file read outside the repo.

### Likelihood Explanation
Likelihood is moderate: it requires the user to click a maliciously crafted `x-github-client://` / `github-mac://` link (a normal, expected Desktop-supported action, not "unnatural user steps"), and requires that the consumer of `filepath` performs a naive path join rather than resolving/validating containment. The existence of `sanitizeCloneName` and its dedicated traversal test-suite for the repo-name component of the exact same URL-parsing path [8](#0-7) [9](#0-8)  shows the maintainers were specifically aware of and hardened against traversal via attacker-supplied URL components in this handler, but the `filepath` field was not covered by an equivalent validator in `parse-app-url.ts`.

### Recommendation
Add an explicit validator for `filepath` in `parseAppURL` (reject `..` segments, absolute paths, drive letters, and backslash/colon path separators — reusing the same defense-in-depth pattern as `sanitizeCloneName`/`testForInvalidChars`), and at the consumption site, resolve the final path against the repository root and assert `resolved.startsWith(repoRoot)` before opening/reading the file, matching the containment check already exercised for clone paths in `clone-path-safety-test.ts`.

### Proof of Concept
1. Register/trigger the protocol handler with a link such as:
   `x-github-client://openRepo/https://github.com/octocat/Hello-World?branch=main&filepath=..%2F..%2F..%2F..%2F.ssh%2Fid_rsa`
2. `parseAppURL` decodes this to `filepath = '../../../.ssh/id_rsa'` with no rejection [1](#0-0) .
3. This is passed to `dispatchURLAction` → `openRepositoryFromUrl` [6](#0-5) , which (per the field's documented purpose) opens this path relative to the cloned repository after clone completes.

**Caveat/uncertainty:** I was unable to retrieve the full body of `openRepositoryFromUrl` in `app/src/ui/dispatcher/dispatcher.ts` (only the dispatch-switch snippet was available) within the available searches, so I cannot confirm with certainty whether that function actually performs a naive `Path.join` without containment checks, or whether it already validates/resolves the path safely elsewhere. This should be verified directly in a full read of `dispatcher.ts`'s `openRepositoryFromUrl` implementation before treating this as confirmed exploitable — I flag this explicitly rather than asserting it as proven.

### Citations

**File:** app/src/lib/parse-app-url.ts (L22-24)
```typescript
  /** the file to open after cloning the repository */
  readonly filepath: string | null
}
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

**File:** app/test/unit/parse-app-url-test.ts (L80-93)
```typescript
    it('adds file path if found', () => {
      const result = parseAppURL(
        'github-mac://openRepo/https://github.com/octokit/octokit.net?branch=master&filepath=Octokit.Reactive%2FOctokit.Reactive.csproj'
      )
      assert.equal(result.name, 'open-repository-from-url')

      const openRepo = result as IOpenRepositoryFromURLAction
      assert.equal(openRepo.url, 'https://github.com/octokit/octokit.net')
      assert.equal(openRepo.branch, 'master')
      assert.equal(
        openRepo.filepath,
        'Octokit.Reactive/Octokit.Reactive.csproj'
      )
    })
```

**File:** app/src/main-process/main.ts (L204-210)
```typescript
app.on('will-finish-launching', () => {
  // macOS only
  app.on('open-url', (event, url) => {
    event.preventDefault()
    handleAppURL(url)
  })
})
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2118-2120)
```typescript
      case 'open-repository-from-url':
        this.openRepositoryFromUrl(action)
        break
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

**File:** app/test/unit/clone-path-safety-test.ts (L43-56)
```typescript
  it('traversal payload clone path stays contained (POSIX)', () => {
    const result = parseRepositoryIdentifier(
      'https://evil.com/owner/x..\\..\\..\\.\\.ssh.git'
    )
    assert(result !== null)
    const safeName = sanitizeCloneName(result.name)
    assert(safeName !== null)
    const baseDir = '/Users/victim/Documents/GitHub'
    const resolved = Path.resolve(Path.join(baseDir, safeName))
    assert(
      resolved.startsWith(Path.resolve(baseDir)),
      `Clone path "${resolved}" escapes base dir`
    )
  })
```
