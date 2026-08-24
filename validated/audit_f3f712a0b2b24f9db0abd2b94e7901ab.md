### Title
Local file disclosure via symlink-following raw `fs.readFile` in `getResolutionDiff` conflict-diff baseline - (File: `app/src/lib/git/diff.ts`)

### Summary
`getResolutionDiff` builds its "baseline" side of a conflict-resolution diff by calling Node's `readFile` directly on `Path.join(repository.path, filePath)`, instead of going through git's object model (`git show`, `getBlobContents`, or `git diff`) as every other diffing code path in this file does. This bypasses git's symlink handling and lets Node follow an on-disk symlink to whatever target it points to.

### Finding Description
`getResolutionDiff` reads the working-tree file as the diff baseline: [1](#0-0) 

This is a raw OS-level file read. For every *other* diff computation in the same file (`getWorkingDirectoryDiff`, target-side stage reads via `getBlobContents`), content comes from git itself: [2](#0-1) [3](#0-2) 

Git treats a tracked symlink (mode `120000`) as a blob whose *content is the literal target path string* — `git diff`/`git show` never follow it to read the linked file. `fs.readFile`, however, follows OS symlinks transparently. If the working directory contains an actual filesystem symlink at `filePath` (created because a cloned/fetched repository tracks that path as a symlink) that points outside the repository (e.g. `../../../../etc/passwd` or an absolute path), `readFile(Path.join(repository.path, filePath), 'utf8')` at line 460 will silently follow it and return the *target file's* contents rather than the symlink's target string. That content becomes `baseContent`/`oldContents`, is diffed with `git diff --no-index` against temp files, and is rendered to the user in the Copilot Conflicts "Changes" tab.

`filePath` originates from `file.path` of entries in `conflictedFiles` (`WorkingDirectoryFileChange[]`), which are conflicted paths reported by git status for the current merge/rebase: [4](#0-3) [5](#0-4) 

This breaks the implicit "diffs reflect git-tracked object content, not raw filesystem content" invariant that the rest of `diff.ts` maintains, and it is reachable whenever the user opens this dialog for a conflicted path whose on-disk entry is a symlink.

### Impact Explanation
If an attacker can get a victim to merge/rebase against a crafted branch/fork such that a path is left conflicted (`ours`/`theirs`/Copilot resolution flow) *and* that path is checked out as a symlink pointing outside the repository root (e.g. to sensitive local files), opening the Copilot Conflicts dialog for that file causes the target file's content to be read off disk and displayed in the diff UI — a local file disclosure that violates the repo-root containment expected of diff rendering.

### Likelihood Explanation
Exploitation requires: (1) the attacker's repository content to produce a genuine merge/rebase conflict on a path, (2) that path resolving on disk as a symlink whose target escapes the repository, and (3) the user actively opening the Copilot Conflicts dialog and viewing that specific file with an `ours`/`theirs`/Copilot resolution selected. All three conditions are plausible via crafted commits/branches (in-scope "attacker controls a cloned/fetched repository"), but require specific merge-conflict engineering around a symlinked path, which is a narrower setup than a plain file conflict. I was not able to fully verify, within the available tooling, the exact git merge-conflict classification behavior for symlink type/target conflicts (e.g., whether git checks out a dangling working-tree symlink unmodified in that state on all platforms), so likelihood should be treated as plausible but not fully confirmed end-to-end.

### Recommendation
In `getResolutionDiff`, before calling `readFile` on the working-tree path, `lstat` the path and reject (or read via a symlink-safe mechanism, e.g. read the git index/blob content for that path via `getBlobContents`) if it is a symlink, mirroring how `git diff`/`git show` treat symlinks as literal target-path text rather than following them. Alternatively, resolve the real path and verify it stays within `repository.path` before reading.

### Proof of Concept
Not independently executed; conceptual PoC based on code review:
1. Prepare a repository where a branch introduces a tracked symlink at path `evil-link` pointing to `../../../../etc/passwd` (or another sensitive absolute/relative path).
2. Craft the merge/rebase such that `evil-link` ends up as a conflicted entry in the victim's working directory after merging/rebasing with a local branch that also touches `evil-link` (e.g., a modify/modify or type-change conflict).
3. Victim opens the Copilot Conflicts dialog, selects `evil-link`, and picks `ours`/`theirs`/Copilot resolution.
4. `getResolutionDiff` at `app/src/lib/git/diff.ts:460-463` calls `readFile(Path.join(repository.path, 'evil-link'), 'utf8')`, which follows the OS symlink and returns the contents of `/etc/passwd` (or whatever the link targets), displayed as the diff baseline in the UI. [6](#0-5)

### Citations

**File:** app/src/lib/git/diff.ts (L392-400)
```typescript
  const { stdout, stderr } = await git(
    args,
    repository.path,
    'getWorkingDirectoryDiff',
    { successExitCodes, encoding: 'buffer' }
  )
  const lineEndingsChange = parseLineEndingsWarning(stderr)

  return buildDiff(stdout, repository, file, 'HEAD', 'HEAD', lineEndingsChange)
```

**File:** app/src/lib/git/diff.ts (L447-463)
```typescript
export async function getResolutionDiff(
  repository: Repository,
  filePath: string,
  options: { content: string } | { stage: 'ours' | 'theirs' },
  hideWhitespaceInDiff: boolean = false
): Promise<IResolutionDiff> {
  const gitStage =
    'stage' in options ? (options.stage === 'ours' ? ':2' : ':3') : undefined

  // Always diff against the working-tree file (which still has conflict
  // markers). This gives a consistent baseline for all three resolution
  // choices (Copilot, current, incoming) so the user sees exactly what each
  // option changes relative to the file's current state on disk.
  const baseContent = await readFile(
    Path.join(repository.path, filePath),
    'utf8'
  )
```

**File:** app/src/lib/git/diff.ts (L476-486)
```typescript
  } else {
    // Stage mode — read the chosen side from the merge index.
    // If the blob doesn't exist (e.g. file deleted on that side in a
    // modify/delete conflict), use empty content to show full deletion.
    try {
      const buffer = await getBlobContents(repository, gitStage, filePath)
      targetContent = buffer.toString('utf-8')
    } catch {
      targetContent = ''
    }
  }
```

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-changes.tsx (L160-187)
```typescript
    if (choice === 'ours' || choice === 'theirs') {
      this.setState({
        diff: null,
        fileContents: null,
        noResolution: false,
        diffError: false,
      })
      try {
        const result = await getResolutionDiff(
          this.props.repository,
          file.path,
          { stage: choice },
          this.state.hideWhitespaceInDiff
        )

        if (this.mounted && requestId === this.diffRequestId) {
          this.setState({
            diff: result.diff,
            fileContents: this.buildFileContents(file, result),
          })
        }
      } catch (e) {
        log.error('Failed to compute resolution diff', e)
        if (this.mounted && requestId === this.diffRequestId) {
          this.setState({ diff: null, fileContents: null, diffError: true })
        }
      }
      return
```

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-changes.tsx (L211-230)
```typescript
    try {
      const result = await getResolutionDiff(
        this.props.repository,
        file.path,
        { content: resolution.resolvedContent },
        this.state.hideWhitespaceInDiff
      )

      if (this.mounted && requestId === this.diffRequestId) {
        this.setState({
          diff: result.diff,
          fileContents: this.buildFileContents(file, result),
        })
      }
    } catch (e) {
      log.error('Failed to compute resolution diff', e)
      if (this.mounted && requestId === this.diffRequestId) {
        this.setState({ diff: null, fileContents: null, diffError: true })
      }
    }
```
