## Title
Unanchored `binaryListRegex` in `getDetectedBinaryFiles` lets an attacker-controlled filename forge entries in the binary-file path list, causing wrong-path binary/merge-driver handling — (File: `app/src/lib/git/diff.ts`)

### Summary
`getDetectedBinaryFiles` parses the output of `git diff --numstat -z <ref>` with the regex `binaryListRegex = /-\t-\t(?:\0.+\0)?([^\0]*)/gi` [1](#0-0)  and `Array.from(stdout.matchAll(binaryListRegex), m => m[1])` is trusted as the list of binary paths that changed. The regex is not anchored to a record boundary (start of string or preceding `\0` terminator), so `matchAll` can find the literal byte sequence `-\t-\t` (dash, tab, dash, tab) *inside* an attacker-chosen filename rather than at the actual `<added>\t<deleted>\t` numstat prefix. Git only forbids `/` and NUL in tracked filenames, so a tab character embedded in a path is legal.

### Finding Description
The `-z` numstat record format is `<added>\t<deleted>\t<path>\0` (or, for renames, `-\t-\t\0<old>\0<new>\0`). `binaryListRegex` looks for the literal substring `-\t-\t` anywhere in the buffer with no anchoring (`^`, previous `\0`, or `lastIndex` boundary checks), and then captures everything up to the next `\0` as the "path": [2](#0-1) .

If an attacker commits a *text* (non-binary) file whose name contains the exact byte sequence `-\t-\t` (e.g. a file literally named `a-<TAB>-<TAB>b`), its numstat line looks like:
```
5\t3\ta-\t-\tb\0
```
Reading this as a flat string, the substring `-\t-\t` occurs starting right after the `a`, *inside the filename*, not at the intended record prefix. `matchAll` will happily match there, and `[^\0]*` will then capture the remainder of the filename up to the next NUL — in this example the fragment `b` — as an entry in the returned "binary paths" array, even though:
1. The file is not binary at all.
2. The captured string (`b`) is not the real path of the changed file (`a-\t-\tb`); it is an unrelated fragment that may coincidentally equal (or collide with) the path of a completely different file in the repository.

The optional non-capturing group `(?:\0.+\0)?` compounds this: JavaScript's `.` matches NUL by default (NUL is not a line-terminator), so when a genuine rename/copy record is present (`-\t-\t\0old\0new\0`), the greedy `.+` can span across the record's own NUL terminators and swallow subsequent, unrelated numstat records in the same `matchAll` pass, further scrambling which path ends up associated with which match.

### Impact Explanation
The output of `getDetectedBinaryFiles` feeds `getBinaryPaths`, which is consumed in `app/src/lib/git/status.ts` to decide whether a conflicted file is binary: `conflictDetails.binaryFilePaths.includes(path)` [3](#0-2) . If a forged path fragment happens to match the path of a real, unrelated conflicted text file, Desktop will treat that file as binary and skip conflict-marker counting/merge-editor handling for it (`parseConflictedState` returns the "binary" branch instead of computing `conflictMarkerCount`) [4](#0-3) . A user relying on Desktop's conflict UI could then commit/push a file that still contains literal `<<<<<<<`/`=======`/`>>>>>>>` conflict markers without realizing it — silent corruption of what the user commits, which is within the defined valid-impact set.

### Likelihood Explanation
Triggering the false match only requires the attacker to control a filename in the fetched/cloned repository (fully within an unprivileged attacker's control — repository content). No local access, no unusual git config, and no rename-detection flags are strictly required for the "text file whose name embeds `-\t-\t`" variant, since it depends purely on string content, not on git enabling rename detection. The more powerful cross-record swallowing variant (via the greedy `\0.+\0` optional group) additionally requires rename/copy detection to be active for the diff (not the default for a bare `git diff --numstat -z <ref>` invocation without `-M`/`-C` unless the user's `diff.renames` config is enabled), which somewhat lowers likelihood for that specific variant, but the primary "fake record" variant does not have that precondition.

### Recommendation
Anchor `binaryListRegex` to actual record boundaries instead of scanning for the literal `-\t-\t` substring anywhere in the buffer — e.g. split the buffer on `\0` first (as `parseRawLogWithNumstat`/`git-delimiter-parser` do for similar output) and then validate each record's `added`/`deleted` fields equal `-` before treating the remainder as the path, rather than relying on an unanchored regex over the whole raw string. Also avoid `.` matching NUL by not depending on greedy dot-matches spanning `\0` at all.

### Proof of Concept
1. Initialize a repo and commit a text file whose name contains the literal bytes `-\t-\t` (dash, TAB, dash, TAB), e.g. `` `a-\t-\tb` `` (creatable programmatically, e.g. via Node `fs.writeFileSync('a-\t-\tb', 'text')`, then `git add`/`git commit`).
2. Modify that file's contents and run:
   ```
   git diff --numstat -z HEAD
   ```
   The raw stdout bytes are: `5\t3\ta-\t-\tb\0` (counts will vary).
3. Feed this exact string into `binaryListRegex`/`getDetectedBinaryFiles`'s matching logic:
   ```js
   const binaryListRegex = /-\t-\t(?:\0.+\0)?([^\0]*)/gi
   const stdout = "5\t3\ta-\t-\tb\u0000"
   console.log(Array.from(stdout.matchAll(binaryListRegex), m => m[1]))
   // -> [ "b" ]
   ```
   The function reports `"b"` as a changed/binary path even though: (a) no such path exists, (b) the file is not binary, and (c) the actually changed file is `a-\t-\tb`. If a real file literally named `b` exists and is conflicted, `getBinaryPaths` would incorrectly include it, causing `status.ts` to treat it as a binary conflict. [5](#0-4) [6](#0-5)

### Citations

**File:** app/src/lib/git/diff.ts (L964-978)
```typescript
/**
 * Runs diff --numstat to get the list of files that have changed and which
 * Git have detected as binary files
 */
async function getDetectedBinaryFiles(repository: Repository, ref: string) {
  const { stdout } = await git(
    ['diff', '--numstat', '-z', ref],
    repository.path,
    'getBinaryPaths'
  )

  return Array.from(stdout.matchAll(binaryListRegex), m => m[1])
}

const binaryListRegex = /-\t-\t(?:\0.+\0)?([^\0]*)/gi
```

**File:** app/src/lib/git/status.ts (L83-127)
```typescript
function parseConflictedState(
  entry: UnmergedEntry,
  path: string,
  conflictDetails: ConflictFilesDetails
): ConflictedFileStatus {
  switch (entry.action) {
    case UnmergedEntrySummary.BothAdded: {
      const isBinary = conflictDetails.binaryFilePaths.includes(path)
      if (!isBinary) {
        return {
          kind: AppFileStatusKind.Conflicted,
          entry,
          conflictMarkerCount:
            conflictDetails.conflictCountsByPath.get(path) || 0,
        }
      } else {
        return {
          kind: AppFileStatusKind.Conflicted,
          entry,
        }
      }
    }
    case UnmergedEntrySummary.BothModified: {
      const isBinary = conflictDetails.binaryFilePaths.includes(path)
      if (!isBinary) {
        return {
          kind: AppFileStatusKind.Conflicted,
          entry,
          conflictMarkerCount:
            conflictDetails.conflictCountsByPath.get(path) || 0,
        }
      } else {
        return {
          kind: AppFileStatusKind.Conflicted,
          entry,
        }
      }
    }
    default:
      return {
        kind: AppFileStatusKind.Conflicted,
        entry,
      }
  }
}
```
