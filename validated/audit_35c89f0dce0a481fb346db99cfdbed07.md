### Title
Index desync in `--numstat`/`--raw` parsing can silently misattribute file paths and change stats to the wrong `CommittedFileChange` - (File: `app/src/lib/git/log.ts`)

### Summary
`parseRawLogWithNumstat` in `app/src/lib/git/log.ts` builds a `files` array while walking the `:`-prefixed `--raw` records, then in a second pass over the `--numstat` records it looks up `files[numStatCount]` by position, with no check that `numStatCount` is within the bounds of (or correctly aligned with) `files`. This mirrors the Sherlock report's root cause: a piece of state (`assetSupply` there, `files.length`/ordinal alignment here) is assumed to always be non-empty/aligned, and the one place that consumes it under a boundary condition has no guard.

### Finding Description [1](#0-0) 

```
export function parseRawLogWithNumstat(
  stdout: string,
  sha: string,
  parentCommitish: string
) {
  const files = new Array<CommittedFileChange>()
  ...
  const lines = stdout.split('\0')

  for (let i = 0; i < lines.length - 1; i++) {
    const line = lines[i]
    if (line.startsWith(':')) {
      ...
      files.push(new CommittedFileChange(path, mapStatus(...), sha, parentCommitish))
    } else {
      const match = /^(\d+|-)\t(\d+|-)\t/.exec(line)
      const [, added, deleted] = forceUnwrap('Invalid numstat line', match)
      ...
      if (isCopyOrRename(files[numStatCount].status)) {   // <-- unchecked index
        i += 2
      }
      numStatCount++
    }
  }
```

This function parses the output of `git log --raw --numstat -z` for a specific SHA (`getChangedFiles`, called from `git-store.ts` when a user browses commit history from a fetched/cloned remote), i.e. it consumes data that is fully controlled by the content of the commit object graph an attacker can construct in a repository the victim clones, fetches, or checks out a PR from. The code trusts that:
1. The `--raw` section always produces exactly one `files` entry per `--numstat` line, in the same order, and
2. `numStatCount` never exceeds `files.length - 1` when it is used to index `files[numStatCount]`.

Neither invariant is enforced anywhere before the indexing. If a crafted commit (e.g., one containing unusual entries such as submodule/mode-only changes, type changes, or numstat/raw records that don't map 1:1 due to git's own edge cases with `-z` output boundaries) produces more numstat records than raw records, `files[numStatCount]` becomes `undefined`, and `isCopyOrRename(undefined.status)` throws a `TypeError`. Short of a hard crash, any partial desync (fewer records mismatched by an off-by-one) causes `files[numStatCount]` to reference the **wrong** `CommittedFileChange`, so the "is this a rename/copy" check and the subsequent `i += 2` skip logic are applied to the wrong file — silently reassigning added/deleted line counts and rename detection to an unrelated path in the parsed changeset.

Unlike other parsing paths in the same file (`parseRawLogWithNumstat` itself, lines 291-307) that use `forceUnwrap` to fail loudly on malformed input, this particular access has no such guard — an inconsistency that strongly suggests the missing check was simply overlooked, exactly like the missing `assetSupply == 0` guard in `_redeem` versus the guard already present in `getCreateAmount`.

### Impact Explanation
The parsed `files` array (with its `path`, `oldPath`, and status) is consumed by history/commit UI (e.g. `app/src/ui/history/selected-commits.tsx`) to drive file-system actions such as "Open file" / reveal-in-Finder/Explorer, which join `repository.path` with `file.path` directly (`Path.join(repository.path, file.path)`), and by "Revert this commit" / restore flows. If the desync causes a rename/copy or path to be attributed to the wrong record, the app can silently display/act on a different file than the one actually changed at that position in the commit, which is a "silent corruption of what the user commits/pushes" class issue if that mis-mapped file is later staged, reverted, or committed based on the wrong `oldPath`/`path`/status pairing. At minimum, in the fully-out-of-bounds case, it is an uncaught `TypeError` when viewing a maliciously crafted commit's history — though a pure crash alone would not meet the impact bar; the concerning path is the *silent* mis-association scenario.

### Likelihood Explanation
Reaching the desync requires crafting a commit whose combined `--raw`/`--numstat -z` output does not maintain a strict 1:1, in-order correspondence between raw entries and numstat entries (e.g. via submodule changes, mode-only changes, or unusual file content that shifts how many `\0`-delimited records are emitted). This is plausible but not trivial to construct with 100% confidence without directly testing against real `git` output edge cases, and I could not fully verify a concrete git invocation that produces such a mismatch from the indexed code alone — this is the main uncertainty in this finding. A background Devin session with a full checkout and the ability to run `git log --raw --numstat -z` against crafted commits would be needed to confirm an actual desync input exists.

### Recommendation
- Add a bounds/consistency check before indexing `files[numStatCount]` (e.g. `if (numStatCount >= files.length) { throw/forceUnwrap(...) }`), consistent with the `forceUnwrap` guards already used elsewhere in the same function.
- Consider deriving the raw-entry/numstat-entry correspondence by parsed path rather than positional index, to avoid relying on an implicit ordering guarantee from `git`'s output.

### Proof of Concept
Not independently confirmed against live `git` output (see Likelihood Explanation above). The structural PoC is: construct a commit whose `git log <sha> -C -M -m -1 --no-show-signature --first-parent --raw --format=format: --numstat -z` output yields more numstat records than raw `:`-prefixed records (or a differently-ordered set), then call `getChangedFiles(repository, sha)` — `parseRawLogWithNumstat` will either throw a `TypeError` on `files[numStatCount].status` or silently attribute the wrong status/path to a numstat record.

**Confidence caveat:** this is the strongest analog I could find in the indexed portions of the codebase matching the report's bug class (a missing guard on a positional/count invariant fed by untrusted, attacker-supplied repository data), but I was not able to fully verify with an actual `git` transcript that the raw/numstat desync is reachable in practice, nor trace every downstream consumer of `CommittedFileChange.path`/`oldPath` to confirm a concrete file-system-impacting misuse. Given index size limits, some related files (e.g. all callers of `getChangedFiles`/`CommittedFileChange`) may not be fully covered by my search; a Devin session with full repository access would be needed to conclusively validate or refute exploitability.

### Citations

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
