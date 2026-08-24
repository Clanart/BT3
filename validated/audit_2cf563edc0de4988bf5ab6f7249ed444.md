## Finding: Symlink-based arbitrary file read via `getResolutionDiff` in the Copilot conflict-resolution diff preview

### Title
Symlink escape allows arbitrary host file read in Copilot conflict-resolution diff preview - (File: app/src/lib/git/diff.ts)

### Summary
`getResolutionDiff()` reads the working-tree file backing a merge conflict directly via `Path.join(repository.path, filePath)` + `fs.readFile`, with **no containment check**. Elsewhere in the same codebase, functionally identical file reads that also originate from repository-controlled paths (`copilot-conflict-context.ts`, `app-store.ts`'s Copilot-resolution writer) explicitly wrap the path in `resolveWithin()` specifically to defend against "path traversal and symlink escapes." `getResolutionDiff` was not given the same guard, leaving an inconsistent, unprotected path in the same conflict-resolution feature set.

### Finding Description
`getResolutionDiff` builds the "baseline" content shown in the Copilot merge-conflict result dialog by reading the on-disk conflicted file directly: [1](#0-0) 

```
const baseContent = await readFile(
  Path.join(repository.path, filePath),
  'utf8'
)
```

`filePath` is the repository-relative path of a conflicted `WorkingDirectoryFileChange`, sourced from `git status` output for a repository the attacker fully controls (e.g. a repo the victim clones or that is merged during a Copilot-assisted conflict resolution). Node's `fs.readFile`/`Path.join` follow symbolic links by default, so if the attacker commits a symlink at that path pointing outside the repository (e.g. to `~/.ssh/id_rsa`, `~/.aws/credentials`, shell rc files, etc.), this call will transparently read the *target* file's contents rather than anything inside the repo.

This is called from the Copilot conflict-resolution "Changes" tab whenever a file is selected: [2](#0-1) 

The resulting `oldContents`/diff content is then rendered directly in the UI (and fed to the syntax highlighter) via `buildFileContents`: [3](#0-2) 

The codebase's own comments show this exact class of bug is understood and was deliberately mitigated *elsewhere*: `buildConflictContext` guards the same kind of read with `resolveWithin`, explicitly citing "path traversal and symlink escapes": [4](#0-3) 

and the writer for Copilot's resolved content in `app-store.ts` does the same before writing: [5](#0-4) 

`resolveWithin` performs `realpath` resolution on both the root and the target and rejects anything that escapes the root: [6](#0-5) 

`getResolutionDiff` does not call `resolveWithin` (or any equivalent check) before reading `baseContent`, so the containment guard that protects the rest of this exact feature (reading conflict context, writing resolved content) is missing on this specific read path.

### Impact Explanation
A malicious repository can plant a symlink at a path that will appear as a conflicted file during a merge/rebase. When the victim opens the Copilot conflict-resolution dialog and selects that file, Desktop reads and displays the contents of an arbitrary file on the victim's filesystem (limited only by OS file permissions of the Desktop process) inside the diff/preview panel in the renderer. This is an out-of-repo file read triggered purely by opening/merging an attacker-controlled repository — no local access, credentials, or additional user action beyond normal repo/PR interaction is required. Sensitive file contents (SSH keys, cloud credentials, config files) could be exposed in the UI and potentially further leaked if the user shares a screenshot, or via any secondary flow that persists the "resolved" content elsewhere.

### Likelihood Explanation
Likelihood is moderate: it requires (1) the victim to encounter a genuine merge/rebase conflict against attacker-influenced content (e.g., merging a malicious branch/PR) and (2) the victim to open the Copilot conflict-resolution "Changes" tab and select the planted file — both are normal, expected user actions within Desktop's advertised conflict-resolution workflow, not unnatural steps. The attacker fully controls the repository content (symlink placement and naming) so the setup cost is low.

### Recommendation
Apply the same `resolveWithin(repository.path, filePath)` containment check used in `copilot-conflict-context.ts` and `app-store.ts` before the `readFile` call in `getResolutionDiff` (and audit sibling working-directory readers such as `getWorkingDirectoryImage` and `getNewFileContent`/`readPartialFile` in `app/src/ui/diff/syntax-highlighting/index.ts` for the same gap), rejecting or refusing to read paths whose realpath resolves outside the repository root.

### Proof of Concept
1. Attacker creates a repository with a branch that, when merged with the victim's branch, produces a conflict at path `notes.txt`.
2. On the attacker's side, `notes.txt` is committed as a symlink (`ln -s ~/.ssh/id_rsa notes.txt` or a Windows junction/`.lnk`-equivalent reparse point) rather than a regular file, so after checkout it resolves to a target outside the repo.
3. Victim, using GitHub Desktop, merges/rebases with the attacker's branch, hits the conflict, and opens the Copilot conflict-resolution dialog, selecting `notes.txt` in the Changes tab.
4. `loadDiffForFile` → `getResolutionDiff(repository, "notes.txt", ...)` executes `readFile(Path.join(repository.path, "notes.txt"), 'utf8')`, which follows the symlink and returns the contents of the victim's `~/.ssh/id_rsa`.
5. The content is rendered in the diff preview panel, exposing the secret file contents to the victim's screen (and to any code path that persists `fileContents`/diff state).

### Citations

**File:** app/src/lib/git/diff.ts (L456-463)
```typescript
  // Always diff against the working-tree file (which still has conflict
  // markers). This gives a consistent baseline for all three resolution
  // choices (Copilot, current, incoming) so the user sees exactly what each
  // option changes relative to the file's current state on disk.
  const baseContent = await readFile(
    Path.join(repository.path, filePath),
    'utf8'
  )
```

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-changes.tsx (L153-187)
```typescript
  private async loadDiffForFile(file: CommittedFileChange) {
    const requestId = ++this.diffRequestId
    const choice = getResolutionChoiceForFile(
      file.path,
      this.props.manualResolutions
    )

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

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-changes.tsx (L239-256)
```typescript
  private buildFileContents(
    file: CommittedFileChange,
    result: IResolutionDiff
  ): IFileContents {
    // Mirror the standard getFileContents path, which caps the amount of
    // content it hands to the syntax-highlighting worker. We only allow
    // expansion when the whole (untruncated) resolution fits under the limit.
    const canBeExpanded =
      result.newContents.length <= MaxDiffExpansionNewContentLength
    const oldContents = result.oldContents
      .slice(0, MaxDiffExpansionNewContentLength)
      .split(/\r?\n/)
    const newContents = result.newContents
      .slice(0, MaxDiffExpansionNewContentLength)
      .split(/\r?\n/)

    return { file, oldContents, newContents, canBeExpanded }
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

**File:** app/src/lib/stores/app-store.ts (L7233-7239)
```typescript
      const absolutePath = await resolveWithin(repository.path, resolution.path)
      if (absolutePath === null) {
        log.warn(
          `Copilot resolution skipped: path outside repository: ${resolution.path}`
        )
        continue
      }
```

**File:** app/src/lib/path.ts (L64-72)
```typescript
  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
}
```
