## Title
`resolveWithin`'s prefix-only boundary check lets a repo-tracked symlink escape the working directory and leak sibling-directory file contents to Copilot conflict resolution - (File: `app/src/lib/path.ts`)

### Summary
`_resolveWithin` in `app/src/lib/path.ts` validates that a resolved path stays inside a root directory using a bare `String.prototype.startsWith` comparison with no path-separator boundary check, exactly the class of "broken comparison that quietly turns an out-of-bounds value into an accepted one" seen in the reported `shr` argument-order bug. This function is the sole guard used before `buildConflictContext` reads conflicted files and forwards their contents to the Copilot merge-conflict-resolution model, so a maliciously crafted, cloned/fetched repository can defeat the guard and cause Desktop to read and exfiltrate a file from a sibling directory to a third-party (Copilot) service.

### Finding Description
`_resolveWithin` computes the real, symlink-resolved paths of both the root and the candidate path, then does: [1](#0-0) 

```
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
```

This check has no trailing separator appended to `realRoot`, so any `realResolved` whose string representation merely *starts with the same characters* as `realRoot` — even though it is a completely different, sibling directory (e.g. `realRoot = ".../GitHub/myrepo"` and `realResolved = ".../GitHub/myrepo-secrets/id_rsa"`) — passes the "inside the root" check. This is the same class of bug as the reported `shr` issue: a security-relevant boundary computation is subtly wrong, so an attacker-influenced value that should be rejected is instead treated as valid.

`resolveWithin` is used as the guard for the Copilot conflict-resolution feature: [2](#0-1) 

`file.path` here comes from the repository's own conflicted-file list (`WorkingDirectoryFileChange` entries derived from `git status`), which is fully attacker-influenced when the conflict arises from merging/fetching a malicious remote branch: [3](#0-2) [4](#0-3) 

Git natively supports tracking symlinks as blobs whose content is an arbitrary target string (including `../` traversal). An attacker can commit a symlinked file whose target points to a sibling directory outside the repository (e.g. another repository the victim has cloned into the same parent folder, per GitHub Desktop's default `~/Documents/GitHub/<repo>` layout), and arrange for that path to be flagged as conflicted during a merge/rebase/cherry-pick against the malicious branch. When Desktop resolves the conflict list through `resolveWithin`, `realpath` follows the symlink to the real, out-of-repo target, and the flawed `startsWith` check accepts it as long as the sibling directory's name shares the root directory's name as a string prefix.

### Impact Explanation
If accepted, `buildConflictContext` proceeds to `stat` and `readFile` the out-of-repo target and includes its content as `rawContent` in the `ICopilotConflictContext`, which is subsequently sent to the Copilot backend via `copilotStore.resolveConflicts`: [5](#0-4) 

This constitutes exfiltration of file contents from outside the current repository (potentially credentials, SSH keys, or other repositories' private files sitting in a sibling folder) to a remote AI service, triggered purely by the victim resolving a merge conflict that originated from an attacker-controlled repository/branch — satisfying the "attacker controls a cloned/fetched repository... result is ... credential/token exfiltration" impact bar. The same broken guard is also reachable from the `x-github-client://openRepo/...?filepath=` deep link handler in `app/src/ui/dispatcher/dispatcher.ts` (`openRepositoryFromUrl`), though that path only calls `shell.showItemInFolder`, a lower-severity file-existence disclosure.

### Likelihood Explanation
Exploitation requires the victim to clone/fetch a malicious repository, encounter a merge/rebase/cherry-pick conflict, and use the Copilot "Resolve with AI" feature — plausible for any project that uses AI-assisted conflict resolution — combined with a sibling directory whose name shares a prefix with the repository's directory name. This is a realistic occurrence given GitHub Desktop's convention of cloning related repos (forks, `-private`, `-secrets`, `.wiki` companions, org variants) into a common parent folder, but it is a probabilistic/naming-dependent precondition rather than a universally guaranteed escape, which somewhat limits (but does not eliminate) likelihood.

### Recommendation
Fix the boundary check in `_resolveWithin` to require a path separator (or exact equality) after the root prefix, e.g.:
```
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
This mirrors the correct fix pattern in the original report (fixing the exact operand/boundary used in a security check rather than trusting a naive substring/argument relationship).

### Proof of Concept
1. Victim has previously cloned `github.com/victim-org/myrepo` into `~/Documents/GitHub/myrepo`, and separately has `~/Documents/GitHub/myrepo-secrets` (or any sibling dir whose name starts with `myrepo`) containing a sensitive file `id_rsa`.
2. Attacker (with push/PR access or via a malicious fork the victim merges) adds a symlink `evil-link` inside `myrepo` pointing to `../myrepo-secrets/id_rsa`, and arranges a conflicting change to that same path on another branch so it appears as a merge conflict.
3. Victim fetches/merges the attacker's branch, hits the conflict, and clicks "Resolve with Copilot".
4. `buildConflictContext` calls `resolveWithin(repository.path, 'evil-link')`; `realpath` resolves the symlink to `.../myrepo-secrets/id_rsa`; `realResolved.startsWith(realRoot)` returns `true` because `"myrepo-secrets"` starts with `"myrepo"`.
5. `readFile` reads `id_rsa`'s contents and it is sent to the Copilot backend as part of the conflict-resolution prompt, exfiltrating it outside the repository and off the user's machine. [6](#0-5)

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

**File:** app/src/lib/stores/app-store.ts (L6549-6570)
```typescript
      const conflictedFiles = getConflictedFiles(
        state.changesState.workingDirectory,
        conflictState.manualResolutions
      )

      if (conflictedFiles.length === 0) {
        log.warn(
          'AppStore: resolveConflictsWithCopilot called with no conflicted files'
        )
        return null
      }

      log.info(
        `[Timing] resolving ${conflictedFiles.length} conflicted file(s)`
      )

      const context = await this.gatherConflictResolutionContext(
        repository,
        labels,
        conflictedFiles,
        state
      )
```

**File:** app/src/lib/stores/app-store.ts (L6576-6587)
```typescript
      const modelRequest = await this.resolveCopilotModelRequest(
        this.getSelectedCopilotModels(account)['conflict-resolution'] ?? null
      )
      try {
        const result = await this.copilotStore.resolveConflicts(
          account,
          context,
          repository.path,
          modelRequest,
          onProgress,
          signal
        )
```

**File:** app/src/lib/stores/app-store.ts (L6660-6676)
```typescript
    // Enrich file entries with delete-vs-modify metadata so
    // buildConflictContext includes them instead of skipping.
    const filesWithDeleteInfo = conflictedFiles.map(f => {
      const deletedSide = getDeletedSideFromStatus(f)
      return deletedSide !== undefined
        ? { path: f.path, deletedSide }
        : { path: f.path }
    })

    const contextTimer = startTimer('build conflict context', repository)
    const fileContext = await buildConflictContext(
      labels.ourLabel,
      labels.theirLabel,
      repository.path,
      filesWithDeleteInfo
    )
    contextTimer.done()
```

**File:** app/test/unit/path-test.ts (L65-78)
```typescript
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
```
