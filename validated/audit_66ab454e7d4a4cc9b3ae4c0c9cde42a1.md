## Title
Copilot merge-conflict resolution follows attacker-committed symlinks, disclosing (and enabling exfiltration of) files outside the repository - (File: `app/src/lib/git/diff.ts`)

## Summary
`getResolutionDiff()` reads the "current" side of a merge conflict directly off disk with `readFile(Path.join(repository.path, filePath), 'utf8')` before it is fed to the diff viewer and to the "Copilot" AI conflict-resolution feature. Neither this call nor its caller (`getManualResolutionMenuItems`/`copilot-conflicts-changes.tsx`) validates that `filePath` refers to a regular file inside the working tree rather than a symlink whose target escapes the repository. Because a git-tracked symlink is checked out as a literal filesystem symlink, and Node's `fs.readFile` transparently follows symlinks, a malicious repository/branch that is merged can point a "conflicted" path at an arbitrary file on the victim's machine (e.g. `~/.ssh/id_rsa`, `~/.aws/credentials`) and have its contents read, displayed as the "current"/"base" diff content, and — if the victim accepts a resolution and commits — potentially written into the commit that is later pushed.

## Finding Description
`getResolutionDiff` is invoked when the user opens the "Resolve with Copilot" / manual conflict resolution UI for a conflicted file: [1](#0-0) 

The `filePath` argument originates from the working-directory conflict entry (`WorkingDirectoryFileChange.path` / `ConflictsWithMarkers`), which in turn is populated straight from `git status --porcelain=2` output: [2](#0-1) 

This path string is trusted to be a plain relative path within the repository and is joined directly with `repository.path` and handed to Node's `readFile`, which follows symlinks by default: [3](#0-2) 

The broken invariant: the code assumes every entry reported by `git status` refers to a real, contained file, but git also tracks and checks out symbolic links (`mode 120000`) as literal filesystem symlinks. An attacker who controls a branch/PR that the victim merges can commit a symlink at a conflicting path whose target is an absolute path (or a `../../..` relative path) pointing outside the repository. When the merge produces a conflict at that path and the victim opens Desktop's conflict-resolution UI, `readFile` follows the symlink and returns the contents of the external target file, not the symlink text itself.

Contrast this with the code paths that already defend against exactly this class of bug — `resolveWithin`/`resolveWithinPosix` explicitly `realpath` both the root and the resolved path and reject anything that escapes via a symlink: [4](#0-3) 
and the dispatcher's `openRepositoryFromUrl` handler that uses it for the deep-link `filepath` parameter: [5](#0-4) 

None of that guard is applied on the merge-conflict / diff-reading path. `getResolutionDiff`, `getNewFileContent` (used for syntax highlighting), and `getWorkingDirectoryImage` all perform a bare `Path.join(repository.path, file.path)` followed directly by `readFile`: [6](#0-5) [7](#0-6) 

## Impact Explanation
This satisfies the "read outside the repo via attacker-controlled repository content" impact class: merging a crafted branch causes GitHub Desktop to read arbitrary files from the victim's filesystem. Beyond mere disclosure in the UI, the same content is fed into `getResolutionDiff`'s `baseContent`, which is used as the diff baseline for the accepted resolution; if the victim (or the Copilot assist flow) accepts "current" as the resolution and commits, the previously-external file's content can end up staged and committed — and, if the victim then pushes, exfiltrated to the attacker-controlled remote/fork the merge came from. This is a credential/secret-exfiltration-via-push scenario, not just local disclosure.

## Likelihood Explanation
Requires the victim to merge a branch/PR that introduces a conflicting path which is (or was previously) a symlink, and then to use the "Resolve conflicts" / Copilot UI on that path rather than resolving purely from the command line. Git checks out tracked symlinks as real symlinks by default on macOS/Linux (subject to `core.symlinks`, which defaults to `true`); Windows requires Developer Mode or admin privileges for symlink checkout, reducing likelihood there. The scenario fits the required threat model (attacker controls a fetched/merged repository object) without requiring local access, malware, or leaked credentials — only ordinary use of the conflict-resolution feature on a hostile merge.

## Recommendation
Before calling `readFile` on any working-directory path derived from git status/conflict data in `getResolutionDiff`, `getNewFileContent`, and `getWorkingDirectoryImage`, verify the target is not a symlink escaping the repository — e.g. `lstat` the resolved path and reject (or resolve safely via `resolveWithin`, which already performs the necessary `realpath` containment check) if it is a symlink or if `realpath` output does not start with the repository's real path.

## Proof of Concept
1. Attacker prepares a branch that, when merged into the victim's checked-out branch, produces a merge conflict at path `notes/todo.txt`.
2. In the attacker's branch, `notes/todo.txt` is committed as a symlink (`git add -m 120000` / `ln -s ~/.ssh/id_rsa notes/todo.txt; git add notes/todo.txt`) pointing to a sensitive absolute path on a typical victim machine, or a relative path such as `../../../../.ssh/id_rsa` if the repo location is predictable.
3. Victim clones/fetches the attacker's branch or PR into GitHub Desktop and performs "Merge into current branch"; git checks out the symlink into the working tree because it's the conflicting/incoming side.
4. Victim opens the Changes/merge-conflict panel and clicks the conflicted file to view/resolve it; Desktop calls `getResolutionDiff(repository, 'notes/todo.txt', ...)`, which executes `readFile(Path.join(repository.path, 'notes/todo.txt'), 'utf8')` — Node follows the symlink and returns the contents of `~/.ssh/id_rsa`.
5. The private key contents are rendered as the "current" diff content in the UI; if the victim accepts/commits this resolution and pushes, the key material is written into the attacker-observable remote history.

### Citations

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

**File:** app/src/lib/git/diff.ts (L926-936)
```typescript
export async function getWorkingDirectoryImage(
  repository: Repository,
  file: FileChange
): Promise<Image> {
  const contents = await readFile(Path.join(repository.path, file.path))
  return new Image(
    contents.buffer,
    contents.toString('base64'),
    getMediaType(Path.extname(file.path)),
    contents.length
  )
```

**File:** app/src/lib/status-parser.ts (L101-119)
```typescript
// 1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
const changedEntryRe =
  /^1 ([MADRCUTX?!.]{2}) (N\.\.\.|S[C.][M.][U.]) (\d+) (\d+) (\d+) ([a-f0-9]+) ([a-f0-9]+) ([\s\S]*?)$/

function parseChangedEntry(field: string): IStatusEntry {
  const match = changedEntryRe.exec(field)

  if (!match) {
    log.debug(`parseChangedEntry parse error: ${field}`)
    throw new Error(`Failed to parse status line for changed entry`)
  }

  return {
    kind: 'entry',
    statusCode: match[1],
    submoduleStatusCode: match[2],
    path: match[8],
  }
}
```

**File:** app/src/lib/path.ts (L64-71)
```typescript
  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
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

**File:** app/src/ui/diff/syntax-highlighting/index.ts (L89-94)
```typescript
  if (file instanceof WorkingDirectoryFileChange) {
    return readPartialFile(
      Path.join(repository.path, file.path),
      0,
      MaxHighlightContentLength - 1
    )
```
