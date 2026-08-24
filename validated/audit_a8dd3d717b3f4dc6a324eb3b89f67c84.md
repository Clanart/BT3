### Title
Unbounded array index in commit-diff numstat parsing causes uncaught exception on malicious repository history - ([File: app/src/lib/git/log.ts])

### Summary
`parseRawLogWithNumstat`, which parses the combined `--raw --numstat -z` output of `git log` for a commit's changed files, indexes into the `files` array using a counter (`numStatCount`) that is derived purely from iterating the numstat block of git's output, without ever checking that the index is within the bounds of `files` (which is populated from the separate raw block). This is the same bug class as the CometBFT advisory: an attacker-influenced index value is trusted and used to index an array before validating it is in range, and when it is not, the code dereferences `undefined` and throws instead of failing safely.

### Finding Description
`getChangedFiles` runs `git log <sha> -C -M -m -1 --raw --numstat -z` and feeds the output to `parseRawLogWithNumstat`: [1](#0-0) 

The parser walks the NUL-separated output twice in a single loop: lines starting with `:` are "raw" entries that get pushed into the `files` array, and any other line is treated as a "numstat" entry that updates `linesAdded`/`linesDeleted` and increments `numStatCount`: [2](#0-1) 

The critical statement is: [3](#0-2) 

`files[numStatCount]` is accessed directly, with no `forceUnwrap`/bounds check — unlike every other value extracted in this same function (`srcMode`, `dstMode`, `status`, `oldPath`, `path` are all wrapped in `forceUnwrap`, which throws a controlled, descriptive error). If `numStatCount` ever reaches or exceeds `files.length` (i.e., the numstat block contains more entries than the raw block produced, or the two blocks become desynchronized for any reason — malformed/unexpected diff shapes such as merge-related output combined with `-m -1 --first-parent`, unusual rename/copy detection edge cases, or future git behavior changes), `files[numStatCount]` evaluates to `undefined`, and `.status` on `undefined` throws an uncaught `TypeError: Cannot read properties of undefined (reading 'status')`. This is functionally identical to the ASA-2024-011 pattern: an index sourced from a peer-influenced data stream (there: `ValidatorIndex` in a `Vote`; here: the implicit numstat/raw alignment in a crafted commit's diff), used to dereference an array without validating the index is in-bounds, causing an unhandled crash instead of a graceful rejection.

### Impact Explanation
This code path is triggered any time a user views a commit's changed files (`History` view / commit details) or commit diff for a repository the user cloned or fetched, since `getChangedFiles` is invoked from the diff-loading logic in `app-store.ts` and `diff.ts`. A malicious or compromised remote could craft a commit whose raw/numstat output triggers this misalignment, resulting in an unhandled exception. Because no `try/catch` wraps `parseRawLogWithNumstat` at the calling sites found (`app/src/lib/stores/app-store.ts`, `app/src/lib/git/diff.ts`), the exception would propagate up as an unhandled promise rejection/render-time exception, disrupting the app for the currently active repository view — a denial-of-service against the app process. There is no evidence of file write, arbitrary code execution, or credential exposure; the impact is limited to a functional crash of the app for that repository session.

### Likelihood Explanation
Likelihood is uncertain: I could not construct/prove a concrete git command output where the raw block and numstat block produced by `git log --raw --numstat -z -M -C -m -1 --first-parent` become misaligned under normal git behavior, since git guarantees a 1:1 correspondence between raw and numstat entries for ordinary diffs. This is analogous to the CometBFT report's own caveat that the invalid condition normally cannot be produced by "nodes running upstream code" — it would require either a git behavior edge case (e.g. certain submodule/typechange/conflict combinations) or a maliciously modified git binary/output source, which is a weaker attacker model than a straightforward crafted-repository primitive. I was not able to fully verify a concrete reproduction path within the available exploration budget.

### Recommendation
Add the same `forceUnwrap` (or an explicit length check that fails gracefully, e.g. by throwing a descriptive parse error or skipping the malformed entry) around the `files[numStatCount]` access, consistent with the rest of the function:
```ts
const fileEntry = forceUnwrap('Invalid log output (numstat/raw mismatch)', files[numStatCount])
if (isCopyOrRename(fileEntry.status)) {
  i += 2
}
```
This converts a silent/uncontrolled `TypeError` into the same handled, descriptive error path already used elsewhere in this parser, and any caller-level `try/catch` wrapping (which should also be verified/added around `getChangedFiles` consumers) can then present a clean error instead of crashing.

### Proof of Concept
Not fully verified — I was unable to confirm, within available tool budget, a concrete `git log --raw --numstat -z` output for a real commit that produces more numstat entries than raw entries. The vulnerable code path is demonstrated statically: [3](#0-2) 
A test harness for `parseRawLogWithNumstat` could feed a synthetic string where a numstat-shaped line (`"1\t0\tfile\0"`) appears without a corresponding preceding raw line (starting with `:`), which would make `numStatCount` reach `files.length` and throw when `files[numStatCount].status` is evaluated. Whether real, unmodified git can produce such output for an attacker-crafted commit was not confirmed.

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

**File:** app/src/lib/git/log.ts (L276-331)
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
```
