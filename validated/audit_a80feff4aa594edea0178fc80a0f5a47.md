## Title
Out-of-bounds array access in `parseRawLogWithNumstat` when numstat entries outnumber raw diff entries - ([File: app/src/lib/git/log.ts])

### Summary
`parseRawLogWithNumstat` in [1](#0-0)  parses the combined `git ... --raw --numstat -z` output into two implicitly-paired sequences: `:`-prefixed raw entries (pushed into the `files` array) and numstat lines (`<added>\t<deleted>\t...`) that are matched to `files` purely by a running counter `numStatCount`. This mirrors the Prysm `blob_cache` bug class: an index (`numStatCount`, analogous to the sidecar/commitments index) is used to read from an array (`files`, analogous to `scs`) whose length is assumed — but never verified — to be at least as long as the number of iterations performed over the other stream.

### Finding Description
In the numstat branch of the loop:
```
} else {
  const match = /^(\d+|-)\t(\d+|-)\t/.exec(line)
  const [, added, deleted] = forceUnwrap('Invalid numstat line', match)
  linesAdded += added === '-' ? 0 : parseInt(added, 10)
  linesDeleted += deleted === '-' ? 0 : parseInt(deleted, 10)

  if (isCopyOrRename(files[numStatCount].status)) {
    i += 2
  }
  numStatCount++
}
``` [2](#0-1) 

`files[numStatCount]` is dereferenced with `.status` without checking that `numStatCount < files.length`. The `files` array is populated only in the sibling `:`-prefixed branch [3](#0-2) . The code implicitly assumes a strict 1:1, in-order correspondence between raw (`:`-prefixed) records and numstat records for every parsed diff. If the raw git output for a crafted/adversarial diff ever contains more numstat lines than raw entries (or a numstat line appears before its corresponding raw entry has been consumed, e.g. due to unusual path encoding, unexpected git diff formatting for certain file/status combinations, or a corrupted/truncated stream), `files[numStatCount]` is `undefined` and `.status` throws `TypeError: Cannot read properties of undefined (reading 'status')`, crashing the parse — exactly the same invariant violation as the Prysm blob cache indexing `scs[i]` without checking it has at least as many elements as `commitments`.

This function's fragility is not hypothetical: the project has already shipped a real-world fix for a related crash in this exact code path, `"[Fixed] Fix "Invalid numstat line" error when trying to preview a pull request - #17267"` [4](#0-3) , showing that externally-influenced diff output (from previewing a pull request, i.e. content coming from a remote/fork) can already break the parser's regex-based assumptions. The `files[numStatCount]` bounds issue is the same class of unguarded assumption in the same function that was never hardened.

### Impact Explanation
`parseRawLogWithNumstat` is invoked from multiple call sites that process diff output influenced by remote/fetched content:
- `getChangedFiles` — viewing a commit's changed files in History [5](#0-4) 
- `getCommitRangeChangedFiles` — diffing a commit range, e.g. when comparing/previewing branches or PRs [6](#0-5) 
- `getBranchMergeBaseChangedFiles` — comparing branches (used for PR preview) [7](#0-6) 
- `getStashedFiles` — viewing stash contents [8](#0-7) 

Since diff content is generated from commits/branches that can originate from a cloned/fetched repository or fork (as in the PR-preview scenario referenced by #17267), an attacker who controls such a repository could craft a commit/tree structure that produces git raw/numstat output triggering `files[numStatCount]` to read past the end of the array, causing an unhandled exception. This is a crash/denial-of-service of the affected UI flow (History view, PR/branch comparison, stash view) rather than memory corruption, since this is TypeScript/Node and out-of-bounds array reads return `undefined` rather than causing undefined behavior — the practical effect is an application-level exception/crash of that operation, not RCE.

### Likelihood Explanation
Moderate-to-low. The regex/parsing brittleness of this exact function has already manifested once as a shipped bug (#17267) from real repository content encountered during PR preview, showing that crafted or unusual diff output reaching this parser is a realistic condition to hit, and no defensive bounds check exists to prevent `files[numStatCount]` from going out of range for any future divergence between raw-entry count and numstat-entry count.

### Recommendation
Add a bounds check before indexing `files[numStatCount]` (e.g. `if (numStatCount >= files.length) { throw new Error('Invalid log output: numstat entry with no matching raw entry') }` or use `files.at(numStatCount)` with a `forceUnwrap`, consistent with the defensive `forceUnwrap` pattern already used elsewhere in this function for other fields) so that a malformed/adversarial diff stream results in a clear, handled error rather than an unguarded property access on `undefined`.

### Proof of Concept
A conceptual repro: construct a repository/commit whose `git diff -C -M -z --raw --numstat` output (as consumed by `parseRawLogWithNumstat`) yields at least one numstat line before/beyond the raw (`:`-prefixed) entries have produced a corresponding `files` entry — e.g. by feeding a hand-crafted `stdout` string directly to `parseRawLogWithNumstat` with a numstat line but no preceding raw line:
```ts
parseRawLogWithNumstat('1\t0\tfile.txt\0', 'sha', 'sha^')
// -> TypeError: Cannot read properties of undefined (reading 'status')
```
This demonstrates the same "index into array shorter than expected" crash primitive as the Prysm `blob_cache` panic, reachable in Desktop through any code path that feeds externally-influenced diff output into `parseRawLogWithNumstat`.

### Citations

**File:** app/src/lib/git/log.ts (L219-245)
```typescript
/** Get the files that were changed in the given commit. */
export async function getChangedFiles(
  repository: Repository,
  sha: string
): Promise<IChangesetData> {
  // opt-in for rename detection (-M) and copies detection (-C)
  // this is equivalent to the user configuring 'diff.renames' to 'copies'
  // NOTE: order here matters - doing -M before -C means copies aren't detected
  const args = [
    'log',
    sha,
    '-C',
    '-M',
    '-m',
    '-1',
    '--no-show-signature',
    '--first-parent',
    '--raw',
    '--format=format:',
    '--numstat',
    '-z',
    '--',
  ]

  const { stdout } = await git(args, repository.path, 'getChangesFiles')
  return parseRawLogWithNumstat(stdout, sha, `${sha}^`)
}
```

**File:** app/src/lib/git/log.ts (L276-334)
```typescript
export function parseRawLogWithNumstat(
  stdout: string,
  sha: string,
  parentCommitish: string
) {
  const files = new Array<CommittedFileChange>()
  let linesAdded = 0
  let linesDeleted = 0
  let numStatCount = 0
  const lines = stdout.split('\0')

  for (let i = 0; i < lines.length - 1; i++) {
    const line = lines[i]
    if (line.startsWith(':')) {
      const lineComponents = line.split(' ')
      const srcMode = forceUnwrap(
        'Invalid log output (srcMode)',
        lineComponents[0]?.replace(':', '')
      )
      const dstMode = forceUnwrap(
        'Invalid log output (dstMode)',
        lineComponents[1]
      )
      const status = forceUnwrap(
        'Invalid log output (status)',
        lineComponents.at(-1)
      )
      const oldPath = /^R|C/.test(status)
        ? forceUnwrap('Missing old path', lines.at(++i))
        : undefined

      const path = forceUnwrap('Missing path', lines.at(++i))

      files.push(
        new CommittedFileChange(
          path,
          mapStatus(status, oldPath, srcMode, dstMode),
          sha,
          parentCommitish
        )
      )
    } else {
      const match = /^(\d+|-)\t(\d+|-)\t/.exec(line)
      const [, added, deleted] = forceUnwrap('Invalid numstat line', match)
      linesAdded += added === '-' ? 0 : parseInt(added, 10)
      linesDeleted += deleted === '-' ? 0 : parseInt(deleted, 10)

      // If this entry denotes a rename or copy the old and new paths are on
      // two separate fields (separated by \0). Otherwise they're on the same
      // line as the added and deleted lines.
      if (isCopyOrRename(files[numStatCount].status)) {
        i += 2
      }
      numStatCount++
    }
  }

  return { files, linesAdded, linesDeleted }
}
```

**File:** changelog.json (L1141-1141)
```json
      "[Fixed] Fix \"Invalid numstat line\" error when trying to preview a pull request - #17267",
```

**File:** app/src/lib/git/diff.ts (L248-292)
```typescript
/**
 * Get the files that were changed for the merge base comparison of two branches.
 * (What would be the result of a merge)
 */
export async function getBranchMergeBaseChangedFiles(
  repository: Repository,
  baseBranchName: string,
  comparisonBranchName: string,
  latestComparisonBranchCommitRef: string
): Promise<IChangesetData | null> {
  const baseArgs = [
    'diff',
    '--merge-base',
    baseBranchName,
    comparisonBranchName,
    '-C',
    '-M',
    '-z',
    '--raw',
    '--numstat',
    '--',
  ]

  const mergeBaseCommit = await getMergeBase(
    repository,
    baseBranchName,
    comparisonBranchName
  )

  if (mergeBaseCommit === null) {
    return null
  }

  const result = await git(
    baseArgs,
    repository.path,
    'getBranchMergeBaseChangedFiles'
  )

  return parseRawLogWithNumstat(
    result.stdout,
    `${latestComparisonBranchCommitRef}`,
    mergeBaseCommit
  )
}
```

**File:** app/src/lib/git/diff.ts (L294-335)
```typescript
export async function getCommitRangeChangedFiles(
  repository: Repository,
  shas: ReadonlyArray<string>,
  useNullTreeSHA: boolean = false
): Promise<IChangesetData> {
  if (shas.length === 0) {
    throw new Error('No commits to diff...')
  }

  const oldestCommitRef = useNullTreeSHA ? NullTreeSHA : `${shas[0]}^`
  const latestCommitRef = shas.at(-1) ?? '' // can't be undefined since shas.length > 0
  const baseArgs = [
    'diff',
    oldestCommitRef,
    latestCommitRef,
    '-C',
    '-M',
    '-z',
    '--raw',
    '--numstat',
    '--',
  ]

  const { stdout, gitError } = await git(
    baseArgs,
    repository.path,
    'getCommitRangeChangedFiles',
    {
      expectedErrors: new Set([GitError.BadRevision]),
    }
  )

  // This should only happen if the oldest commit does not have a parent (ex:
  // initial commit of a branch) and therefore `SHA^` is not a valid reference.
  // In which case, we will retry with the null tree sha.
  if (gitError === GitError.BadRevision && useNullTreeSHA === false) {
    const useNullTreeSHA = true
    return getCommitRangeChangedFiles(repository, shas, useNullTreeSHA)
  }

  return parseRawLogWithNumstat(stdout, latestCommitRef, oldestCommitRef)
}
```

**File:** app/src/lib/git/stash.ts (L278-298)
```typescript
/** Get the files that were changed in the given stash commit */
export async function getStashedFiles(
  repository: Repository,
  stashSha: string
): Promise<ReadonlyArray<CommittedFileChange>> {
  const args = [
    'stash',
    'show',
    stashSha,
    '--raw',
    '--numstat',
    '-z',
    '--format=format:',
    '--no-show-signature',
    '--',
  ]

  const { stdout } = await git(args, repository.path, 'getStashedFiles')

  return parseRawLogWithNumstat(stdout, stashSha, `${stashSha}^`).files
}
```
