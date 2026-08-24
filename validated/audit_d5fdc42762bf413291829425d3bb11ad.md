## Analysis

The Astaria bug is a **broken boundary/invariant check**: an arithmetic comparison that was supposed to bound a value within a safe range but instead lets it slip past the boundary under specific edge conditions, and the code that is supposed to catch that case does the wrong computation. The closest structural analog in GitHub Desktop is the repository-boundary check in `resolveWithin()`, which uses a raw string-prefix (`startsWith`) test instead of an actual path-boundary test.### Title
Repository-boundary check in `resolveWithin()` uses raw string-prefix comparison, allowing deep-link `filepath` to escape the repository root into sibling directories - (File: `app/src/lib/path.ts`)

### Summary
GitHub Desktop's `x-github-client://openRepo/...` deep link handler accepts an attacker-controlled `filepath` query parameter and passes it through `resolveWithin(repository.path, filepath)` before calling `shell.showItemInFolder()` on the result [1](#0-0) . The boundary check inside `resolveWithin` is implemented as a plain string `startsWith` comparison between the resolved real path and the real root path, with no verification that a path separator (or exact equality) follows the root prefix [2](#0-1) . This is the classic "prefix without separator" path-boundary bug: any sibling directory whose name happens to start with the repository directory's name (e.g. `myrepo` vs `myrepo-secrets`) will incorrectly pass the "is within root" check.

### Finding Description
The relevant logic:
```ts
const resolved = resolve(normalizedRoot, normalizedRelative)
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
``` [3](#0-2) 

The comment above the function claims "If the fully resolved path does not reside underneath the root path this method will return null" [4](#0-3) , but the implementation only checks that `realResolved` textually *begins with* the characters of `realRoot`. It never checks that the character immediately following the shared prefix is a path separator (or that the strings are exactly equal). Consequently, given:
- `realRoot = "/Users/victim/Documents/GitHub/myrepo"`
- an attacker-supplied `filepath` such as `../myrepo-secrets/config.json`

`resolve()` will produce `"/Users/victim/Documents/GitHub/myrepo-secrets/config.json"`, and `realResolved.startsWith(realRoot)` evaluates to `true` even though `myrepo-secrets` is a completely separate sibling directory, not a subdirectory of `myrepo`. The function returns the resolved path as if it were safely contained, defeating the entire purpose of the boundary check.

This is structurally identical to the Astaria bug class described in the report: a guard meant to bound a value within a legitimate range (`timeBuffer`/`maxDuration` vs. repository root) is implemented with an off/incorrect comparison that fails precisely at the boundary, letting an out-of-range value through where the guard should have rejected it.

The attacker-controlled entry point is the deep link itself. `parseAppURL()` extracts `filepath` from the `x-github-client://openRepo/...` URL with no validation beyond checking it isn't an absolute path [5](#0-4) , and `openRepositoryFromUrl()` in the dispatcher only blocks absolute paths before calling `resolveWithin` [1](#0-0) . Neither of these guards catch the sibling-prefix case, since `resolveWithin` is the sole authority relied upon to reject out-of-root paths.

Existing unit tests for `resolveWithin` cover `..`/`../..` escapes, null bytes, and symlink escapes, but do not test the sibling-directory-with-shared-prefix case, so the gap is untested and unnoticed [6](#0-5) .

### Impact Explanation
A victim who clicks an attacker-crafted `x-github-client://openRepo/<repo-url>?filepath=...` link (or an "Open in Desktop" button on a malicious web page) can have Desktop reveal/open a file located in a directory *outside* the intended repository, as long as that directory's name shares the repository directory name as a prefix. Since GitHub Desktop's default clone location follows a predictable convention (`~/Documents/GitHub/<repo-name>`), and many developers keep related folders side-by-side with predictable, overlapping naming (`app`, `app-old`, `app-backup`, `project`, `project2`, etc.), this is a realistic file-disclosure primitive: `shell.showItemInFolder()` opens the OS file manager focused on the resolved (out-of-root) file, exposing its existence and location to the user's screen and enabling further interaction with it. This matches the "file read outside the repo" impact category via an attacker-controlled deep link.

### Likelihood Explanation
Exploitation requires: (1) the victim already has a repository cloned that matches the URL in the deep link (or Desktop will fall back to a clone/open flow first), and (2) a sibling directory exists whose name is a prefix-extension of the repository folder name. Condition (2) is not guaranteed for arbitrary victims, which lowers likelihood compared to a universal bypass, but it is a common naming pattern in real developer environments and is entirely outside the attacker's need for local access, admin rights, or prior compromise — the only requirement is that the victim click a link. This satisfies the report's requirement of an unprivileged, remotely-triggerable path (deep link click) with no unnatural user steps.

### Recommendation
Fix the boundary check in `_resolveWithin` (`app/src/lib/path.ts`) to require either exact equality or that the shared prefix is followed by the platform path separator, e.g.:
```ts
return realResolved === realRoot ||
  realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
(using the separator appropriate to the `options` module, i.e. `join`/`Path.sep` equivalent for the posix/win32 variants), and add a regression test asserting that a sibling directory sharing a name prefix (e.g. root `.../myrepo`, target `.../myrepo-evil/file`) is rejected.

### Proof of Concept
1. Victim has previously cloned `https://github.com/victim/myrepo` to the default location `~/Documents/GitHub/myrepo`, and also has an unrelated folder `~/Documents/GitHub/myrepo-secrets/config.json` (a common real-world naming pattern, e.g. backup/config folders).
2. Attacker sends the victim a link:
   `x-github-client://openRepo/https://github.com/victim/myrepo?filepath=..%2Fmyrepo-secrets%2Fconfig.json`
3. Victim clicks the link. `parseAppURL()` parses this into `{ name: 'open-repository-from-url', url: 'https://github.com/victim/myrepo', filepath: '../myrepo-secrets/config.json' }` [5](#0-4) .
4. `openRepositoryFromUrl()` finds/opens the existing `myrepo` repository, then calls `resolveWithin(repository.path, '../myrepo-secrets/config.json')` [7](#0-6) .
5. Inside `_resolveWithin`, `resolved` becomes `~/Documents/GitHub/myrepo-secrets/config.json`; `realResolved.startsWith(realRoot)` is `true` because `"myrepo-secrets"` textually starts with `"myrepo"` [3](#0-2) .
6. The function returns the resolved path instead of `null`, and `shell.showItemInFolder(resolved)` opens the file manager on `config.json`, a file entirely outside the `myrepo` repository, silently defeating the intended containment guard.

### Citations

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

**File:** app/src/lib/path.ts (L23-24)
```typescript
 * If the fully resolved path does not reside underneath the root path
 * this method will return null.
```

**File:** app/src/lib/path.ts (L66-72)
```typescript
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
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

**File:** app/test/unit/path-test.ts (L44-102)
```typescript
  describe('resolveWithin', () => {
    const root = process.cwd()

    it('fails for paths outside of the root', async () => {
      assert((await resolveWithin(root, join('..'))) === null)
      assert((await resolveWithin(root, join('..', '..'))) === null)
    })

    it('succeeds for paths that traverse out, and then back into, the root', async () => {
      assert.equal(await resolveWithin(root, join('..', basename(root))), root)
    })

    it('fails for paths containing null bytes', async () => {
      assert((await resolveWithin(root, 'foo\0bar')) === null)
    })

    it('succeeds for absolute relative paths as long as they stay within the root', async () => {
      const parent = resolve(root, '..')
      assert.equal(await resolveWithin(parent, root), root)
    })

    if (!__WIN32__) {
      it('fails for paths that use a symlink to traverse outside of the root', async () => {
        const tempDir = await mkdtemp(join(tmpdir(), 'path-test'))
        const symlinkName = 'dangerzone'
        const symlinkPath = join(tempDir, symlinkName)

        try {
          await symlink(resolve(tempDir, '..', '..'), symlinkPath)
          assert((await resolveWithin(tempDir, symlinkName)) === null)
        } finally {
          await unlink(symlinkPath)
          await rmdir(tempDir)
        }
      })

      it('succeeds for paths that use a symlink to traverse outside of the root and then back again', async () => {
        const tempDir = await mkdtemp(join(tmpdir(), 'path-test'))
        const symlinkName = 'dangerzone'
        const symlinkPath = join(tempDir, symlinkName)

        try {
          await symlink(resolve(tempDir, '..', '..'), symlinkPath)
          const throughSymlinkPath = join(
            symlinkName,
            basename(resolve(tempDir, '..')),
            basename(tempDir)
          )
          assert.equal(
            await resolveWithin(tempDir, throughSymlinkPath),
            resolve(tempDir, throughSymlinkPath)
          )
        } finally {
          await unlink(symlinkPath)
          await rmdir(tempDir)
        }
      })
    }
  })
```
