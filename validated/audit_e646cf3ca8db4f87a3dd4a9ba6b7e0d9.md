## Title
`listSubmodules` regex mis-splits `path`/`describe` when the submodule's checked-out ref name contains parentheses, corrupting the reported submodule path — ([File: app/src/lib/git/submodule.ts])

### Summary
`listSubmodules` parses the output of `git submodule status --` with the single regex: [1](#0-0) 

`statusRe = /^.([^ ]+) (.+) \((.+?)\)$/gm` assumes the trailing `(describe)` segment contains no parentheses of its own. `git describe` output for a submodule can legitimately contain parentheses because git ref names (branch/tag names) are permitted to include `(` and `)`. If the submodule's checked-out branch/tag name contains a `)` character (or a nested paren pair), the greedy `path` capture `(.+)` backtracks to the *rightmost* ` (...)…)$` boundary in the line rather than the true `describe` boundary, causing part of the real `describe` string to be absorbed into `path`, and vice versa.

### Finding Description
`git submodule status` prints lines like:
```
 <sha> <path> (<describe>)
```
The `<describe>` field comes from `git describe` inside the submodule and reflects whatever ref (tag/branch) is checked out there — content controlled by whoever controls that submodule's remote/repo (an attacker-controlled nested repository referenced from `.gitmodules`). Since git ref names may legally contain parentheses, a maliciously named branch/tag (e.g. `evil (payload)`) produces a status line such as:
```
 <sha> foo/submodule (evil (payload))
```
Applying the regex, the greedy `(.+)` for `path` combined with the `$`-anchored, non-greedy `(.+?)\)$` for `describe` causes JavaScript's backtracking engine to align the split at the *last* ` (` occurrence in the line, not the intended one. Concretely for the line above the parser yields `path = "foo/submodule (evil"` and `describe = "payload)"` instead of `path = "foo/submodule"`, `describe = "evil (payload)"`.

### Impact Explanation
The corrupted `path` value returned by `listSubmodules` is consumed in `GitStore.discardChanges`: [2](#0-1) 

and again later to decide which paths are treated as submodules for checkout purposes: [3](#0-2) 

Because `s.path` no longer equals the real working-directory path reported by `git status` (`file.path`), `foundSubmodule` becomes `false` for a legitimate, currently-checked-out submodule. This causes the discard-changes code path meant for submodules (safe `resetSubmodulePaths`/`checkoutPaths`/`resetPaths` handling) to instead treat the submodule's on-disk directory as a normal file: it will attempt `shell.moveItemToTrash`/`rm` on the submodule directory path, rather than the git-aware submodule reset. This can silently discard/lose the user's local submodule working state in a way inconsistent with what "discard changes" is supposed to do (reset submodule to its recorded commit), matching the "silent loss of local work" impact category. It is not a direct code-execution/exfiltration bug but it is a working-tree-state integrity bug driven entirely by attacker-controlled ref-name content in a submodule the user has cloned.

### Likelihood Explanation
Triggering requires only that the user's repository contain a submodule pointing at an attacker-controlled remote where the currently checked-out ref (branch or tag) has a name containing `(`/`)` — a low-effort, fully attacker-controllable condition (branch/tag creation is not privileged on any repo the attacker controls) and requires no unusual user action beyond adding/using the submodule and later discarding changes. This is a realistic but narrow trigger (requires discard-changes flow specifically, and a submodule with an unusual ref name), so likelihood is moderate rather than trivially "every clone."

### Recommendation
Do not rely on regex-based greedy/lazy backtracking to split `path` from `(describe)`. Instead, parse `git submodule status` with a machine-parsable format (e.g. `git submodule--helper list` or `git submodule status` combined with `.gitmodules` parsing) that doesn't require a free-text `describe` field to be delimited by ambiguous parentheses, or explicitly disallow/escape parentheses when matching, e.g. anchor the SHA (fixed 40-hex-char format) and use a stricter path pattern, or use `git config -f .gitmodules --get-regexp path` to enumerate paths independently of the human-readable `describe` string, then correlate by SHA/status line order rather than free-text splitting.

### Proof of Concept
1. Create a repository `outer` with a submodule `sub` pointing at a remote repository.
2. In `sub`, create and check out a branch literally named `evil (payload)` (git allows this ref name), then push/leave it checked out as `HEAD`.
3. In `outer`, run `git submodule status --`; note the output line: ` <sha> sub (evil (payload))`.
4. Call `listSubmodules(repository)` in GitHub Desktop / a unit test against this fixture, mirroring the existing test pattern in `app/test/unit/git/submodule-test.ts` (which currently only tests simple, paren-free `describe` values such as `'first-tag~2'` and `'heads/feature-branch'`): [4](#0-3) 
5. Assert `result[0].path === 'sub'` — the assertion will fail because `path` will include part of the malicious ref name (e.g. `'sub (evil'`), demonstrating the parsing corruption.

Note: I was only able to verify the single call site of `listSubmodules` in `GitStore.discardChanges`; I could not find any other production usages (`grep_search` returned only `git-store.ts` and the git/submodule module itself), so I cannot confirm additional downstream impact on commit/push flows beyond the discard-changes path described above.

### Citations

**File:** app/src/lib/git/submodule.ts (L190-194)
```typescript
  const statusRe = /^.([^ ]+) (.+) \((.+?)\)$/gm

  for (const [, sha, path, describe] of stdout.matchAll(statusRe)) {
    submodules.push(new SubmoduleEntry(sha, path, describe))
  }
```

**File:** app/src/lib/stores/git-store.ts (L1553-1560)
```typescript
    const submodules = await listSubmodules(this.repository)

    for (const file of files) {
      const foundSubmodule = submodules.some(s => s.path === file.path)

      if (file.status.kind !== AppFileStatusKind.Deleted && !foundSubmodule) {
        if (moveToTrash) {
          try {
```

**File:** app/src/lib/stores/git-store.ts (L1612-1621)
```typescript
    const submodulePaths = pathsToCheckout.filter(p =>
      submodules.find(s => s.path === p)
    )

    // Don't attempt to checkout files that are submodules or don't exist in the index after our reset
    const necessaryPathsToCheckout = pathsToCheckout.filter(
      x =>
        submodulePaths.indexOf(x) === -1 ||
        changedFilesInIndex.get(x) !== IndexStatus.Added
    )
```

**File:** app/test/unit/git/submodule-test.ts (L16-27)
```typescript
    it('returns the submodule entry', async t => {
      const testRepoPath = await setupFixtureRepository(
        t,
        'submodule-basic-setup'
      )
      const repository = new Repository(testRepoPath, -1, null, false)
      const result = await listSubmodules(repository)
      assert.equal(result.length, 1)
      assert.equal(result[0].sha, 'c59617b65080863c4ca72c1f191fa1b423b92223')
      assert.equal(result[0].path, 'foo/submodule')
      assert.equal(result[0].describe, 'first-tag~2')
    })
```
