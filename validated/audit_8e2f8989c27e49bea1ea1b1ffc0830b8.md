### Title
Greedy `statusRe` regex in `listSubmodules` mis-parses submodule path/describe when `git describe` output contains parentheses - (File: `app/src/lib/git/submodule.ts`)

### Summary
`listSubmodules` parses the output of `git submodule status --` with the regex `/^.([^ ]+) (.+) \((.+?)\)$/gm` and constructs `SubmoduleEntry(sha, path, describe)` objects that are later used to decide which paths to reset/update via `resetSubmodulePaths`.

### Finding Description
The regex is:
```js
const statusRe = /^.([^ ]+) (.+) \((.+?)\)$/gm
``` [1](#0-0) 

The middle group `(.+)` (intended to capture the submodule **path**) is greedy and unanchored with respect to internal `(`/`)` characters. Because regex backtracking for a greedy `.+` followed by a literal `\(...\)$` effectively binds to the **last** occurrence of `" ("` in the line (the engine expands the greedy group maximally, then backs off only as far as needed to find a trailing `" (...)"` that reaches end-of-line), any `"("`/`")"` sequence embedded inside the trailing `git describe` text will be greedily consumed into the "path" capture group instead of the "describe" group.

`git describe` output for a submodule is derived from ref/tag names reachable in that submodule's history. Git's `check-ref-format` rules do not forbid parentheses in ref/tag names, so an attacker who controls a submodule's remote (a fetched/cloned submodule) can create a tag such as:
```
v1.0) fakepath (marker
```
When that tag is the exact `HEAD` of the submodule, `git describe` returns the tag name verbatim, producing a `git submodule status` line like:
```
 c59617b65080863c4ca72c1f191fa1b423b92223 foo/submodule (v1.0) fakepath (marker)
```
Applying `statusRe` to this line, the greedy group 2 backtracks to the rightmost `" ("`, which is the one inside the crafted describe text (before `marker)`), not the one that actually delimits the real describe output. As a result:
- `path` is parsed as `foo/submodule (v1.0) fakepath` (truncated/shifted, containing garbage appended from the describe text) instead of the real `foo/submodule`.
- `describe` is parsed as `marker` instead of the full `v1.0) fakepath (marker`.

### Impact Explanation
`SubmoduleEntry.path` from `listSubmodules` is the value later passed to `resetSubmodulePaths`, which runs `git submodule update --recursive --force -- <paths>`. If `path` is corrupted (containing appended text from the crafted describe string), the resulting argv no longer matches the actual on-disk submodule path. In the best case this simply fails to match any pathspec (a no-op or git error), and in the worst case — if a corrupted string happens to coincide with another legitimate path or pathspec pattern in the repository (e.g., a differently named nested submodule/directory) — the wrong submodule/path gets force-reset, causing silent loss of local uncommitted work in a path the user did not intend to touch. This matches the "silent corruption of what the user commits or discards" impact class in scope.

However, achieving a *reliable*, attacker-chosen collision between the corrupted parsed string and another real path requires the corrupted string to exactly match an existing pathspec; this is not guaranteed by the PoC alone and would need to be demonstrated as reliably exploitable (e.g., crafting the tag so that the corrupted "path" exactly equals a second legitimate submodule path in the repository) rather than just showing the parse mismatch.

### Likelihood Explanation
Exploitability requires the victim to add/fetch a submodule pointing at an attacker-controlled remote and have GitHub Desktop enumerate submodules (`listSubmodules` is invoked as part of normal repository status refresh in `git-store.ts`) while that submodule's checked-out ref/tag has a crafted name containing parentheses. Creating such a tag is straightforward for an attacker who controls the submodule remote, and no unusual user interaction beyond adding/using the submodule is required, so likelihood of triggering the parse corruption itself is reasonably high, though turning it into an actual wrong-file-reset requires additional path-collision engineering.

### Recommendation
Replace the greedy path-capture with a bounded/anchored approach, e.g. parse from the right using the last-known-good delimiter more strictly (since the SHA is fixed-width, path should be captured as `.+?` non-greedy up to a `" ("` that is followed only by `.*\)$` with no other reserved characters), or better, invoke `git submodule status` with `-z`/machine-readable NUL-separated output where available, or use `git config -f .gitmodules` plus `git submodule status` per-path in a loop with quoting that avoids ambiguous parenthesis parsing. At minimum, validate that parsed `path` values correspond to actual entries in `.gitmodules` before passing them to `resetSubmodulePaths`.

### Proof of Concept
1. In a submodule remote repository, create and check out a tag with an embedded parenthesis pattern such that the checked-out commit's exact tag name is:
   ```
   v1.0) fakepath (marker
   ```
2. Reference this submodule from a parent repository and fetch/checkout it in GitHub Desktop (or via `git submodule update --init`).
3. Run `git submodule status --` in the parent repo; the output line looks like:
   ```
    <sha> foo/submodule (v1.0) fakepath (marker)
   ```
4. Call `listSubmodules(repository)` and assert:
   ```ts
   assert.notEqual(result[0].path, 'foo/submodule') // path is corrupted, e.g. "foo/submodule (v1.0) fakepath"
   assert.notEqual(result[0].describe, 'v1.0) fakepath (marker') // describe is truncated to "marker"
   ```
This demonstrates `SubmoduleEntry.path` diverging from the real submodule path due to the greedy `statusRe` regex. [2](#0-1) [3](#0-2)

### Citations

**File:** app/src/lib/git/submodule.ts (L158-197)
```typescript
  const { stdout, exitCode } = await git(
    ['submodule', 'status', '--'],
    repository.path,
    'listSubmodules',
    { successExitCodes: new Set([0, 128]) }
  )

  if (exitCode === 128) {
    // unable to parse submodules in repository, giving up
    return []
  }

  const submodules = new Array<SubmoduleEntry>()

  // entries are of the format:
  //  1eaabe34fc6f486367a176207420378f587d3b48 git (v2.16.0-rc0)
  //
  // first character:
  //   - " " if no change
  //   - "-" if the submodule is not initialized
  //   - "+" if the currently checked out submodule commit does not match the SHA-1 found in the index of the containing repository
  //   - "U" if the submodule has merge conflicts
  //
  // then the 40-character SHA represents the current commit
  //
  // then the path to the submodule
  //
  // then the output of `git describe` for the submodule in braces
  // we're not leveraging this in the app, so go and read the docs
  // about it if you want to learn more:
  //
  // https://git-scm.com/docs/git-describe
  const statusRe = /^.([^ ]+) (.+) \((.+?)\)$/gm

  for (const [, sha, path, describe] of stdout.matchAll(statusRe)) {
    submodules.push(new SubmoduleEntry(sha, path, describe))
  }

  return submodules
}
```

**File:** app/src/lib/git/submodule.ts (L199-212)
```typescript
export async function resetSubmodulePaths(
  repository: Repository,
  paths: ReadonlyArray<string>
): Promise<void> {
  if (paths.length === 0) {
    return
  }

  await git(
    ['submodule', 'update', '--recursive', '--force', '--', ...paths],
    repository.path,
    'updateSubmodule'
  )
}
```
