### Title
Unvalidated hunk-header line counts allow a malicious repository to make GitHub Desktop stage/commit different content than what the diff viewer displays - (File: `app/src/lib/diff-parser.ts`)

### Summary
The BakerFi report's broken invariant is: a derived accounting value (`shares`) is computed from a branch condition (`total.elastic == 0`) that doesn't verify the other half of the invariant (`total.base == 0`), so attacker-supplied input (a 1-wei donation) drives the vault into a permanently degenerate state that the code never detects. The Desktop analog is structurally identical: `DiffParser.parseHunk` trusts the unified-diff hunk header's declared `oldLineCount`/`newLineCount` for downstream consumers, while the number of lines it actually parses into `hunk.lines` is derived independently (by reading lines until a non-diff-prefix character is hit). Nothing cross-checks that these two derivations agree.

### Finding Description
`parseHunkHeader` extracts `oldStartLine`, `oldLineCount`, `newStartLine`, `newLineCount` purely from a regex match on the `@@ -l,s +l,s @@` line: [1](#0-0) 

`parseHunk` then seeds two independent rolling counters (`rollingDiffBeforeCounter`, `rollingDiffAfterCounter`) from `header.oldStartLine`/`header.newStartLine` and increments them per parsed line based solely on the line's prefix character (`+`, `-`, or space), continuing until a line without a recognized diff prefix is seen: [2](#0-1) 

At no point does the parser assert that the number of `-`/context lines equals `header.oldLineCount`, or that `+`/context lines equals `header.newLineCount`. The only sanity check is `if (lines.length === 1) throw new Error('Malformed diff, empty hunk')` — a check for a completely empty hunk, not a count mismatch. `parseDiffHeader` also does not validate the `--- a/...` / `+++ b/...` file paths against anything; it merely looks for the `+++` marker: [3](#0-2) 

This unvalidated `DiffHunkHeader` (specifically `oldStartLine`/`newStartLine`) then flows unchanged into `formatPatch`, which is the exact function used to build the partial patch that is fed to `git apply --cached` when a user stages/commits only some of the lines shown in a hunk. `formatPatch` recomputes `oldCount`/`newCount` from the lines it re-emits, but it reuses the *original, unvalidated* `hunk.header.oldStartLine` and `hunk.header.newStartLine` verbatim as the position for the new hunk header: [4](#0-3)  The same pattern exists in the discard-changes patch builder: [5](#0-4) 

Because `git apply`/`git apply --cached` uses fuzzy, context-based matching to locate a hunk in the target blob rather than trusting the header's line number exactly, a hunk whose header start-line/line-count fields are inconsistent with the diff body it precedes can be applied at a different offset than the location the diff viewer rendered to the user. GitHub Desktop's own line-selection UI computes which lines are "selected" using `absoluteIndex = hunk.unifiedDiffStart + lineIndex` (an index into the flattened line array), which is completely decoupled from the header's `oldStartLine`/`newStartLine` fields: [6](#0-5)  Nothing links "the row the user clicked in the UI" back to "the line-number context git will use to locate the hunk", other than the unvalidated header values inherited from the parse step.

### Impact Explanation
The `contents` (git diff output) parsed by `DiffParser` originates from `git diff`/`git log --patch` run by Desktop against the working tree of a repository the user has cloned. If the repository or its checked-out content is attacker-influenced (e.g., via a crafted file whose bytes make git or a configured diff driver emit an internally inconsistent-but-syntactically-valid unified diff, or via a crafted `.gitattributes`-declared diff driver in an untrusted cloned/fetched repo), the resulting `DiffHunkHeader` values can diverge from the actual hunk body without GitHub Desktop detecting it. Because the partial-commit/discard-patch formatters (`formatPatch`, `formatPatchToDiscardChanges`) propagate the unvalidated header start-line values into the exact patch handed to `git apply`, and `git apply` performs fuzzy context matching rather than trusting header offsets, the resulting staged/committed diff can differ from what the diff viewer displayed to the user and from the lines the user explicitly (de)selected — i.e., silent corruption of what the user commits, matching the requested impact class exactly (corrupted git object at commit time, not merely a UI glitch).

### Likelihood Explanation
This requires no local/physical access, no admin rights, and no pre-existing malware — only that the user opens/interacts with a diff produced from an attacker-influenced repository (cloned or fetched) inside Desktop's normal partial-staging workflow. The exact reproduction requires a diff whose header counts are inconsistent with its body (e.g., through a custom diff driver or unusual textconv output triggered by repository content), which is a narrower trigger than the BakerFi bug's trivial "send 1 wei" primitive, so likelihood is lower than the original report but the code path itself has zero validation guarding against it — there is no defense-in-depth check anywhere between `DiffParser.parse` and `formatPatch`/`git apply`.

### Recommendation
- In `parseHunk` (`app/src/lib/diff-parser.ts`), after consuming a hunk's lines, assert that the counted `-`/context lines equal `header.oldLineCount` and the counted `+`/context lines equal `header.newLineCount`; throw a parse error (as is already done for other malformed-diff cases) on mismatch.
- Do not propagate a `DiffHunkHeader` whose declared counts were not verified against the actual parsed body into `formatPatch`/`formatPatchToDiscardChanges`.
- Consider passing `--unified=<n>` and validating the returned diff's file paths (`--- a/`, `+++ b/`) resolve to the same file that was requested, to avoid trusting attacker-shaped diff-driver output outright.

### Proof of Concept
Not independently executable from static analysis alone (would require constructing a repository with a custom `.gitattributes` diff/textconv driver, or a crafted binary, that causes `git diff` to emit a header whose `oldLineCount`/`newLineCount` do not match the number of context/`+`/`-` lines that follow, then verifying that `parseHunk` accepts it without error and that the resulting `formatPatch` output applies (via `git apply --cached`) at an offset different from the one rendered in the diff viewer). Static evidence for the missing validation is in `app/src/lib/diff-parser.ts:239-390` (header parsed independently of body-line counting, no count reconciliation) combined with `app/src/lib/patch-formatter.ts:213-220` and `:320-326` (unvalidated header start-lines reused verbatim when building the patch sent to `git apply`). Confirming the end-to-end exploit (an actual line-offset divergence surviving into a real commit) would require running Desktop's git integration tests against such a crafted repository, which was not available in this static index-based investigation.

### Citations

**File:** app/src/lib/diff-parser.ts (L174-190)
```typescript
  private parseDiffHeader(): IDiffHeaderInfo | null {
    // TODO: There's information in here that we might want to
    // capture, such as mode changes
    while (this.nextLine()) {
      if (this.lineStartsWith('Binary files ') && this.lineEndsWith('differ')) {
        return { isBinary: true }
      }

      if (this.lineStartsWith('+++')) {
        return { isBinary: false }
      }
    }

    // It's not an error to not find the +++ line, see the
    // 'parses diff of empty file' test in diff-parser-tests.ts
    return null
  }
```

**File:** app/src/lib/diff-parser.ts (L239-257)
```typescript
  private parseHunkHeader(line: string): DiffHunkHeader {
    const m = diffHeaderRe.exec(line)
    if (!m) {
      throw new Error(`Invalid hunk header format`)
    }

    // If endLines are missing default to 1, see diffHeaderRe docs
    const oldStartLine = this.numberFromGroup(m, 1)
    const oldLineCount = this.numberFromGroup(m, 2, 1)
    const newStartLine = this.numberFromGroup(m, 3)
    const newLineCount = this.numberFromGroup(m, 4, 1)

    return new DiffHunkHeader(
      oldStartLine,
      oldLineCount,
      newStartLine,
      newLineCount
    )
  }
```

**File:** app/src/lib/diff-parser.ts (L306-377)
```typescript
    let c: DiffLinePrefix | null

    let rollingDiffBeforeCounter = header.oldStartLine
    let rollingDiffAfterCounter = header.newStartLine

    let diffLineNumber = linesConsumed
    while ((c = this.parseLinePrefix(this.peek()))) {
      const line = this.readLine()

      if (!line) {
        throw new Error('Expected unified diff line but reached end of diff')
      }

      // A marker indicating that the last line in the original or the new file
      // is missing a trailing newline. In other words, the presence of this marker
      // means that the new and/or original file lacks a trailing newline.
      //
      // When we find it we have to look up the previous line and set the
      // noTrailingNewLine flag
      if (c === DiffPrefixNoNewline) {
        // See https://github.com/git/git/blob/21f862b498925194f8f1ebe8203b7a7df756555b/apply.c#L1725-L1732
        if (line.length < 12) {
          throw new Error(
            `Expected "no newline at end of file" marker to be at least 12 bytes long`
          )
        }

        const previousLineIndex = lines.length - 1
        const previousLine = lines[previousLineIndex]
        lines[previousLineIndex] = previousLine.withNoTrailingNewLine(true)

        continue
      }

      // We must increase `diffLineNumber` only when we're certain that the line
      // is not a "no newline" marker. Otherwise, we'll end up with a wrong
      // `diffLineNumber` for the next line. This could happen if the last line
      // in the file doesn't have a newline before the change.
      diffLineNumber++

      let diffLine: DiffLine

      if (c === DiffPrefixAdd) {
        diffLine = new DiffLine(
          line,
          DiffLineType.Add,
          diffLineNumber,
          null,
          rollingDiffAfterCounter++
        )
      } else if (c === DiffPrefixDelete) {
        diffLine = new DiffLine(
          line,
          DiffLineType.Delete,
          diffLineNumber,
          rollingDiffBeforeCounter++,
          null
        )
      } else if (c === DiffPrefixContext) {
        diffLine = new DiffLine(
          line,
          DiffLineType.Context,
          diffLineNumber,
          rollingDiffBeforeCounter++,
          rollingDiffAfterCounter++
        )
      } else {
        return assertNever(c, `Unknown DiffLinePrefix: ${c}`)
      }

      lines.push(diffLine)
    }
```

**File:** app/src/lib/patch-formatter.ts (L143-157)
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
```

**File:** app/src/lib/patch-formatter.ts (L213-220)
```typescript
    patch += formatHunkHeader(
      hunk.header.oldStartLine,
      oldCount,
      hunk.header.newStartLine,
      newCount
    )
    patch += hunkBuf
  })
```

**File:** app/src/lib/patch-formatter.ts (L320-326)
```typescript
    patch += formatHunkHeader(
      hunk.header.newStartLine,
      newCount,
      hunk.header.oldStartLine,
      oldCount
    )
    patch += hunkBuf
```
