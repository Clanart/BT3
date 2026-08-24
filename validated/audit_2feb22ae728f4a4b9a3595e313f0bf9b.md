### Title
`resolveWithin` uses an unanchored `startsWith` prefix check, letting deep-link/`file.path` inputs escape the intended repository root to a sibling directory - (File: `app/src/lib/path.ts`)

### Summary
`app/src/lib/path.ts`'s `_resolveWithin()` is the single security boundary GitHub Desktop relies on to guarantee that a user- or attacker-influenced relative path stays "at or underneath" a given root directory. The final containment check is a bare string `startsWith`, which — like the Astaria report's unclamped `maxStrategistFee` being consumed by an unguarded `mulWadDown` — validates against the *wrong* invariant: it accepts any resolved path whose string representation happens to share the root's characters as a prefix, not just paths that are actually nested inside the root directory.

### Finding Description
`_resolveWithin` normalizes and resolves the target path and then performs the actual containment test with: [1](#0-0) 

`realResolved.startsWith(realRoot)` has no trailing separator check. If `realRoot` is `/Users/victim/Documents/GitHub/myrepo` and a sibling directory `/Users/victim/Documents/GitHub/myrepo-secrets` (or `myrepo-old`, `myrepo-fork`, etc. — a very common naming pattern for developers who keep several related clones side by side) exists, then a relative segment such as `../myrepo-secrets/id_rsa` resolves to `/Users/victim/Documents/GitHub/myrepo-secrets/id_rsa`, and:

```
"/Users/victim/Documents/GitHub/myrepo-secrets/id_rsa".startsWith("/Users/victim/Documents/GitHub/myrepo") === true
```

The check passes even though the resolved path is a completely different, sibling directory — exactly the same class of bug as the Astaria report: a bound/containment guard that looks correct in isolation but silently admits values outside the intended range once combined with how it's actually consumed downstream.

This primitive is exercised by the `x-github-client://` deep-link handler, which an attacker fully controls via a link the victim clicks: [2](#0-1) 

The `filepath` parameter of the deep link is only checked for being non-absolute; it is then handed to `resolveWithin(repository.path, filepath)`, and if that call returns non-null, `shell.showItemInFolder(resolved)` is invoked on whatever path was returned — even though the true intent of `resolveWithin` (and every doc-comment around it) is "the resolved path is guaranteed to reside at, or underneath this path" (`repository.path`).

`resolveWithin`/`resolveWithinPosix`/`resolveWithinWin32` are also relied on as the containment guard for reading arbitrary file content in the Copilot merge-conflict flow: [3](#0-2) 

Both call sites assume `resolveWithin` enforces "inside `rootPath`", but the implementation actually enforces "the string starts with the same characters as `rootPath`", which is a materially weaker and incorrectly-scoped guarantee — the exact "wrong invariant used as if it were the strict one" failure mode from the report.

### Impact Explanation
This lets a maliciously crafted `x-github-client://` link (an attacker-controlled deep link the victim clicks — an explicitly in-scope vector) cause Desktop to reveal/open a file that lives outside the intended repository, in an unrelated, attacker-chosen sibling directory on disk, as long as any directory name on the victim's machine happens to share the target repository's name as a prefix (a common occurrence — forks, `-old`/`-backup`/`-private`/`-v2` clones, etc., frequently sit in the same parent folder Desktop uses by default, `~/Documents/GitHub`). This is a "read outside the repo" primitive triggered purely by clicking a link, matching the in-scope impact category directly. The same broken primitive backs the file-read guard used when assembling AI merge-conflict context, so any path capable of reaching `resolveWithin` with attacker-influenced segments inherits the same escape.

### Likelihood Explanation
Exploitation requires no local/physical access, no malware, and no social engineering beyond a single link click (explicitly allowed under "a link or deep link the user clicks"). The only precondition is the existence of a same-prefixed sibling directory, which is an ordinary, unprivileged filesystem state rather than an unnatural user action. The existing guards (`isAbsolute` check on `filepath`, and `resolveWithin`'s own null-byte and traversal normalization) do not stop this path because they operate before the final, insufficiently-anchored `startsWith` comparison — none of them re-validate that the resolved path is separated from the root by a path boundary.

### Recommendation
Change the containment check in `_resolveWithin` (`app/src/lib/path.ts`) to require an exact match or a match followed by the platform path separator, e.g.:
```ts
return realResolved === realRoot ||
  realResolved.startsWith(realRoot + sep)
  ? resolved
  : null
```
where `sep` is drawn from the same `options` object (`Path.sep`, `Path.posix.sep`, or `Path.win32.sep`) used elsewhere in the function, so prefix-sharing sibling directories can no longer satisfy the check.

### Proof of Concept
1. On the victim machine, two directories exist under the default clone location: `~/Documents/GitHub/myrepo` (the repository Desktop has open) and `~/Documents/GitHub/myrepo-secrets` (any pre-existing sibling whose name is prefixed by `myrepo`, e.g. a private fork or backup clone).
2. Attacker sends the victim a link:
   `x-github-client://openRepo/https://github.com/owner/myrepo?filepath=..%2Fmyrepo-secrets%2Fid_rsa`
3. Desktop's protocol handler routes to `Dispatcher.openRepositoryFromUrl`, which opens/finds the `myrepo` repository and then calls:
   `resolveWithin(repository.path, '../myrepo-secrets/id_rsa')`
4. Inside `_resolveWithin`, `resolved` becomes `~/Documents/GitHub/myrepo-secrets/id_rsa`; `realResolved.startsWith(realRoot)` evaluates to `true` because `"...myrepo-secrets/id_rsa"` starts with `"...myrepo"` as a string, despite `myrepo-secrets` being a different, sibling directory.
5. `shell.showItemInFolder(resolved)` is called, revealing/opening a file that resides entirely outside the `myrepo` repository the deep link nominally targeted — demonstrating the file-read/reveal-outside-repo primitive via nothing more than a clicked link.

### Citations

**File:** app/src/lib/path.ts (L64-71)
```typescript
  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1973)
```typescript
  private async openRepositoryFromUrl(action: IOpenRepositoryFromURLAction) {
    const { url, pr, branch, filepath } = action

    let repository: Repository | null

    if (pr !== null) {
      repository = await this.openPullRequestFromUrl(url, pr)
    } else if (branch !== null) {
      repository = await this.openBranchNameFromUrl(url, branch)
    } else {
      repository = await this.openOrCloneRepository(url)
    }

    if (repository === null) {
      return
    }

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
  }
```

**File:** app/src/lib/copilot-conflict-context.ts (L390-407)
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
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path is outside the repository',
        }
      }
```
