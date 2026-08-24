## Analog Found

### Title
Working-directory diff line selections are positional, not content-bound, letting a hook-driven or concurrent working-tree change during a pull/merge cause a silent mismatch between the diff the user approved and the patch actually staged and committed - (File: `app/src/lib/git/apply.ts`)

### Summary
GitHub Desktop lets a user select individual lines/hunks of a file's working-directory diff for a partial commit. That selection is stored as a `DiffSelection` object keyed purely by **numeric line index**, with no binding to the diff's content or a version/hash of the file. When the commit is finally created, Desktop re-computes the diff from disk at that moment and blindly re-applies the old, positional selection to the *new* diff to build the patch that gets staged. If the tracked file's on-disk content changes between the moment the user reviewed/selected lines and the moment the commit executes — which is exactly what Desktop's own hook-execution feature (`post-merge`, `post-checkout`, etc., driven by an attacker-controlled `core.hooksPath`/hook script) can trigger — the stale line indices land on unrelated diff content, and the user silently commits/omits code they never reviewed.

### Finding Description
The partial-commit path is:

1. UI computes a diff and lets the user toggle individual lines via `DiffSelection`, which tracks only `divergingLines: Set<number>` — line-index numbers, with **no content hash or diff identity** binding the selection to a specific diff snapshot: [1](#0-0) [2](#0-1) 

2. When the commit is finally made, `_commitIncludedChanges` passes the (possibly stale) `WorkingDirectoryFileChange` objects — carrying only that positional selection — into `createCommit` → `stageFiles` → `applyPatchToIndex`: [3](#0-2) [4](#0-3) 

3. `applyPatchToIndex` does **not** reuse whatever diff the UI last showed the user. It re-fetches a brand-new diff straight from disk and builds the patch by re-applying the old selection's line indices to this fresh diff: [5](#0-4) 

4. `formatPatch` walks the *new* diff's hunks and lines and asks `file.selection.isSelected(absoluteIndex)` for each — with `absoluteIndex` computed against the new diff's line numbering, not the one the user actually saw: [6](#0-5) 

The broken invariant is identical in shape to the Alchemy report: a validation/selection decision made against "current state" (the diff at the time of user review) is silently reused after that state has changed, because nothing enforces that the state is still valid at consumption time.

The attacker-controlled trigger for that state change is Desktop's own hook-execution feature. Desktop now runs real Git hooks — including `post-merge`, `post-checkout`, `pre-commit`, etc. — for `pull`/`merge`/`commit` operations, resolving the hooks directory from the repository's own `core.hooksPath` config (a value a repository can arrange to point at a tracked, attacker-controlled directory, e.g. via the common `husky`-style workflow triggered by an ordinary `npm install` after cloning): [7](#0-6) [8](#0-7) [9](#0-8) 

A `post-merge` hook run as part of a routine `pull` executes arbitrary attacker-supplied code with write access to the working directory, and can rewrite a tracked file that the user had already diffed and partially selected for commit in the Changes view. Desktop's existing guards do not stop this: there is no re-validation of `DiffSelection` against the diff actually used at apply time, and hook execution is explicitly designed to run with full filesystem access to the repo (`GITHUB_DESKTOP=1` env, `spawn` with the hook's own interpreter) — there is no sandboxing or output-diffing between "diff shown" and "diff staged."

### Impact Explanation
This allows an attacker who controls a hook (via `core.hooksPath` shipped with a repository, or a hook installed through a normal contributor workflow like `husky`) to cause **silent corruption of what the user commits**: content the user explicitly excluded from a partial commit can be swept into the commit (or vice versa), without any error, warning, or diff re-confirmation. This maps directly to the "silent corruption of what the user commits or pushes" category — the user's git history and pushed changes no longer match what they visually approved, which can leak unreviewed/attacker-injected code, secrets, or reintroduce content the user intentionally removed, without any indication in the UI.

### Likelihood Explanation
Moderate. It requires: (1) the user to have partially selected lines of a file for commit, (2) a hook (`post-merge`/`post-checkout`, run via `core.hooksPath`) to modify that same file's content before the commit is finalized, and (3) Desktop to not have refreshed/invalidated the stale `DiffSelection` in a way that forces re-review. Given Desktop's background auto-fetch/pull cadence and the ubiquity of committed hook-directory conventions (husky-style `core.hooksPath`), the timing window is realistic in normal, unprompted usage rather than requiring contrived user steps.

### Recommendation
- Bind `DiffSelection` (or the `WorkingDirectoryFileChange` carrying it) to a content identity of the diff it was computed against (e.g., a hash of the diff/hunks, or the exact diff object itself) rather than raw line indices.
- In `applyPatchToIndex`, verify that the freshly-fetched diff matches the diff the selection was derived from before building the patch; if it doesn't match, fail the operation and force the UI to re-present the new diff for re-selection instead of silently applying stale indices.
- Treat hook-driven working-tree modifications occurring between diff computation and commit as a case requiring forced re-diff/re-confirmation, mirroring the Alchemy fix direction of "don't rely on state that can change later."

### Proof of Concept
1. Clone/open a repository whose `core.hooksPath` points at a tracked directory (e.g. a `husky`-style setup installed via a normal `npm install` after clone) containing a malicious `post-merge` hook that appends/edits lines in `tracked-file.txt`.
2. In Desktop, modify `tracked-file.txt` locally, open the Changes diff, and select only lines 1–5 for the next commit (leave the rest unselected).
3. Before committing, trigger (or wait for Desktop's automatic) `pull`, which fast-forwards/merges and fires the malicious `post-merge` hook; the hook rewrites `tracked-file.txt` on disk, shifting/altering hunks.
4. Click Commit without re-opening the diff. `applyPatchToIndex` (`app/src/lib/git/apply.ts:60-81`) re-diffs the now-modified file and applies the old line-index selection (`app/src/lib/patch-formatter.ts:143-171`) to the new hunks.
5. Inspect the resulting commit: it contains lines the user never reviewed/approved (or omits lines they intended to include), with no warning shown by Desktop.

### Citations

**File:** app/src/models/diff/diff-selection.ts (L74-84)
```typescript
  /**
   * @param divergingLines Any line numbers where the selection differs from the default state.
   * @param selectableLines Optional set of line numbers which can be selected.
   */
  private constructor(
    private readonly defaultSelectionType:
      | DiffSelectionType.All
      | DiffSelectionType.None,
    private readonly divergingLines: Set<number> | null = null,
    private readonly selectableLines: Set<number> | null = null
  ) {}
```

**File:** app/src/models/diff/diff-selection.ts (L121-136)
```typescript
  /** Returns a value indicating wether the given line number is selected or not */
  public isSelected(lineIndex: number): boolean {
    const lineIsDivergent =
      !!this.divergingLines && this.divergingLines.has(lineIndex)

    if (this.defaultSelectionType === DiffSelectionType.All) {
      return !lineIsDivergent
    } else if (this.defaultSelectionType === DiffSelectionType.None) {
      return lineIsDivergent
    } else {
      return assertNever(
        this.defaultSelectionType,
        `Unknown base selection type ${this.defaultSelectionType}`
      )
    }
  }
```

**File:** app/src/lib/stores/app-store.ts (L3686-3689)
```typescript
    const files = state.changesState.workingDirectory.files
    const selectedFiles = files.filter(file => {
      return file.selection.getSelectionType() !== DiffSelectionType.None
    })
```

**File:** app/src/lib/git/commit.ts (L15-31)
```typescript
export async function createCommit(
  repository: Repository,
  message: string,
  files: ReadonlyArray<WorkingDirectoryFileChange>,
  options?: {
    amend?: boolean
    noVerify?: boolean
    signOff?: boolean
    allowEmpty?: boolean
  } & HookCallbackOptions
): Promise<string> {
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)
```

**File:** app/src/lib/git/apply.ts (L52-83)
```typescript
  const applyArgs: string[] = [
    'apply',
    '--cached',
    '--unidiff-zero',
    '--whitespace=nowarn',
    '-',
  ]

  const diff = await getWorkingDirectoryDiff(repository, file)

  if (diff.kind !== DiffType.Text && diff.kind !== DiffType.LargeText) {
    const { kind } = diff
    switch (diff.kind) {
      case DiffType.Binary:
      case DiffType.Submodule:
      case DiffType.Image:
        throw new Error(
          `Can't create partial commit in binary file: ${file.path}`
        )
      case DiffType.Unrenderable:
        throw new Error(
          `File diff is too large to generate a partial commit: ${file.path}`
        )
      default:
        assertNever(diff, `Unknown diff kind: ${kind}`)
    }
  }

  const patch = await formatPatch(file, diff)
  await git(applyArgs, repository.path, 'applyPatchToIndex', { stdin: patch })

  return Promise.resolve()
```

**File:** app/src/lib/patch-formatter.ts (L143-171)
```typescript
    hunk.lines.forEach((line, lineIndex) => {
      const absoluteIndex = hunk.unifiedDiffStart + lineIndex

      // We write our own hunk headers
      if (line.type === DiffLineType.Hunk) {
        return
      }

      // Context lines can always be let through, they will
      // never appear for new files.
      if (line.type === DiffLineType.Context) {
        hunkBuf += `${line.text}\n`
        oldCount++
        newCount++
      } else if (file.selection.isSelected(absoluteIndex)) {
        // A line selected for inclusion.

        // Use the line as-is
        hunkBuf += `${line.text}\n`

        if (line.type === DiffLineType.Add) {
          newCount++
        }
        if (line.type === DiffLineType.Delete) {
          oldCount++
        }

        anyAdditionsOrDeletions = true
      } else {
```

**File:** app/src/lib/hooks/get-repo-hooks.ts (L54-65)
```typescript
const getHooksPath = async (path: string) =>
  resolve(
    path,
    (await git(['rev-parse', '--git-path', 'hooks'], path)).replace(
      /\r?\n$/,
      ''
    )
  )

const getConfigValue = (path: string, key: string) =>
  git(['config', '-z', '--get', key], path).then(x => x.split('\0')[0])

```

**File:** app/src/lib/git/pull.ts (L43-57)
```typescript
  let opts: IGitStringExecutionOptions = {
    env: await envForRemoteOperation(remote.url),
    // git pull triggers merge or rebase hooks depending on config, instead of
    // trying to check pull.rebase and friends we'll just intercept all possible
    // hooks that could be run as part of a pull operation.
    interceptHooks: [
      'pre-merge-commit',
      'prepare-commit-msg',
      'commit-msg',
      'post-merge',
      'pre-rebase',
      'pre-commit',
      'post-rewrite',
    ],
  }
```

**File:** app/src/lib/hooks/hooks-proxy.ts (L245-264)
```typescript
    const { code, signal } = await new Promise<{
      code: number | null
      signal: NodeJS.Signals | null
    }>((resolve, reject) => {
      const child = spawn(gitPath, args, {
        cwd: proxyCwd,
        // GITHUB_DESKTOP lets hooks know they're run from GitHub Desktop.
        // See https://github.com/desktop/desktop/issues/19001
        env: { ...shellEnv.env, ...safeEnv, GITHUB_DESKTOP: '1' },
        signal: abortController.signal,
      })
        .on('close', (code, signal) => resolve({ code, signal }))
        .on('error', err => reject(err))

      // git-hook run takes care of ensuring we only get hook output on stderr
      // https://github.com/git/git/blob/4cf919bd7b946477798af5414a371b23fd68bf93/hook.c#L73C6-L73C22
      child.stderr.pipe(conn.stderr, { end: false }).on('error', reject)
      child.stderr.on('data', data => terminalOutput.push(data))
      conn.stdin.pipe(child.stdin).on('error', reject)
    })
```
