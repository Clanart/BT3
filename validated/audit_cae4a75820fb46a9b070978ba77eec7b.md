### Title
`hasHiddenBidiChars` is hardcoded to `false` for binary/header-only diffs, letting attacker-controlled rename paths hide malicious Unicode without warning - (File: `app/src/lib/diff-parser.ts`)

### Summary
`DiffParser.parse()` computes `hasHiddenBidiChars` by testing the *entire* raw diff text against `HiddenBidiCharsRegex` — but only on the code path that reaches the final `return` statement. On the two earlier `return` paths (binary diff and diff with no `+++` line, i.e. "empty diff") the flag is hardcoded to `false` even though the `header` string returned alongside it (which contains the `diff --git a/... b/...` line and, for renames/copies, the `rename from` / `rename to` lines) is attacker-controlled content coming straight from a cloned/fetched repository and is never scanned for the same characters. [1](#0-0) 

This is structurally identical to the Panoptic bug: a security check (`T/N` spread check / here, the bidi-char scan) is applied on the "normal" path but is silently skipped on a boundary condition (`N == 0` / here, "binary" or "no +++ line found"), even though the corrupting input (`total liquidity` / here, the diff `header` text) is still present and unguarded.

### Finding Description
`HiddenBidiCharsRegex` exists specifically to catch invisible bidirectional-override Unicode characters (`\u202A-\u202E`, `\u2066-\u2069`) that can make a file or path visually spoof its real content/extension — this is the same class of attack documented at `https://github.co/hiddenchars` and referenced directly in the code comment. [2](#0-1) 

The check is only actually executed once, at the very end of `parse()`, against `text` (the full diff buffer): [3](#0-2) 

But there are two earlier exit points in the same function that return a fully-formed `IRawDiff` object — including the `header` field, which is attacker-controlled text taken from `git diff`/`git log --patch` output for a file the attacker fully controls (its name, and for renames, its old/new path) — while unconditionally setting `hasHiddenBidiChars: false`:

- The "empty diff" branch (no `+++` line found, e.g. a rename-only or mode-only change, or an empty new file): [4](#0-3) 

- The binary-file branch (git reports `Binary files ... differ`): [5](#0-4) 

In both cases `header` (which is returned to the caller and ultimately surfaced through `IRawDiff.header`, `PathLabel`/`PathText` renaming display, and the diff header UI) can still contain bidi-override characters embedded in a crafted file/rename path, but the flag consumers rely on to decide whether to warn the user is force-set to `false`.

The only place that surfaces this flag to the user is `DiffContentsWarning`, which strictly gates the "This diff contains bidirectional Unicode text…" banner on `diff.hasHiddenBidiChars`: [6](#0-5) 

Because binary diffs and empty/rename-only diffs never populate this flag from actual content, a file whose *path* (not its contents) carries hidden bidi characters (e.g., a rename from `evil.js` to a path using an RLO override so it displays as `evil.exe.js`⟵`sj.exe`-looking text, or a binary file given a spoofed extension via bidi override in its name) will never trigger the existing "hidden characters" safeguard, even though the exact same mechanism (`HiddenBidiCharsRegex`) is present and enforced for ordinary text-diff content.

### Impact Explanation
Desktop's hidden-Unicode warning is a security control meant to stop a specific, well-known class of attack: using invisible bidi-override characters in file names/paths to make a reviewer believe they are looking at (or committing) something different from what is actually there. Because the check is bypassed on the header-only/binary code paths, an attacker who controls a repository, branch, or PR (a cloned/fetched repository is explicitly in scope) can rename or add a binary file with a spoofed path and have GitHub Desktop render it in the Changes list, History view, and diff header without the protective banner that would otherwise alert the user. This does not corrupt commit content client-side, but it silently defeats an existing security guard intended to prevent visual spoofing of what a user is about to commit, push, or review — the same "checked on the normal path, silently skipped on the edge-case path" pattern as the referenced report.

### Likelihood Explanation
Likelihood is moderate: the attacker only needs to control the contents of a repository the victim clones/fetches or a PR/branch they review (renaming a file, or adding a binary file with a crafted path) — no local access, admin rights, or social engineering beyond a normal collaboration workflow is required. However, the exploitation value is limited to a UI trust/warning bypass (the underlying bidi-regex mechanism is otherwise present and effective for regular text-diff content), so it is a lower-severity variant compared to the flagged content actually altering the commit.

### Recommendation
In `app/src/lib/diff-parser.ts`, run `HiddenBidiCharsRegex.test(...)` against `header` (not a hardcoded `false`) on both early-return paths (the "empty diff"/no-`+++`-line branch and the binary branch), so that rename/copy paths and file names embedded in the diff header are always scanned, matching the coverage already given to hunk content:

```ts
if (!headerInfo) {
  return {
    header,
    contents: '',
    hunks: [],
    isBinary: false,
    maxLineNumber: 0,
    hasHiddenBidiChars: HiddenBidiCharsRegex.test(header),
  }
}

if (headerInfo.isBinary) {
  return {
    header,
    contents: '',
    hunks: [],
    isBinary: true,
    maxLineNumber: 0,
    hasHiddenBidiChars: HiddenBidiCharsRegex.test(header),
  }
}
```

### Proof of Concept
1. In a repository, rename a file such that the new path contains a `U+202E` (RIGHT-TO-LEFT OVERRIDE) character sequence designed to visually spoof the extension, e.g. rename `payload` to a path containing `\u202Egnp.exe` so it displays reversed as `exe.png`.
2. Commit this rename in a repository the victim will clone or fetch (or open as a PR).
3. In GitHub Desktop, view the change: since a rename with no other content change produces a diff whose header has no `+++` line captured before end-of-text in some cases, or where the changed file is binary, `DiffParser.parse` takes the early-return branch and force-sets `hasHiddenBidiChars: false` (`app/src/lib/diff-parser.ts:408-429`) despite `header` containing the crafted path.
4. `DiffContentsWarning` (`app/src/ui/diff/diff-contents-warning.tsx:49-53`) therefore never renders the "hidden Unicode" alert, while the renamed path is displayed via `PathLabel`/`PathText` as if it were a normal, safely-named file — exactly the situation the bidi-char warning exists to prevent.

Note: I could not find, within the indexed portions of the codebase, any additional sanitization of `IRawDiff.header` or of rename paths elsewhere (e.g. in `path-text.tsx`) that would independently catch this case; if such a mitigation exists in code not covered by the index, it should be verified directly in a full checkout.

### Citations

**File:** app/src/lib/diff-parser.ts (L25-30)
```typescript
/**
 * Regular expression matching invisible bidirectional Unicode characters that
 * may be interpreted or compiled differently than what it appears. More info:
 * https://github.co/hiddenchars
 */
export const HiddenBidiCharsRegex = /[\u202A-\u202E]|[\u2066-\u2069]/
```

**File:** app/src/lib/diff-parser.ts (L408-429)
```typescript
      // empty diff
      if (!headerInfo) {
        return {
          header,
          contents: '',
          hunks: [],
          isBinary: false,
          maxLineNumber: 0,
          hasHiddenBidiChars: false,
        }
      }

      if (headerInfo.isBinary) {
        return {
          header,
          contents: '',
          hunks: [],
          isBinary: true,
          maxLineNumber: 0,
          hasHiddenBidiChars: false,
        }
      }
```

**File:** app/src/lib/diff-parser.ts (L449-456)
```typescript
      return {
        header,
        contents,
        hunks,
        isBinary: headerInfo.isBinary,
        maxLineNumber: getLargestLineNumber(hunks),
        hasHiddenBidiChars: HiddenBidiCharsRegex.test(text),
      }
```

**File:** app/src/ui/diff/diff-contents-warning.tsx (L45-53)
```typescript
  private getTextDiffWarningItems(): ReadonlyArray<DiffContentsWarningItem> {
    const items = new Array<DiffContentsWarningItem>()
    const { diff } = this.props

    if (diff.hasHiddenBidiChars) {
      items.push({
        type: DiffContentsWarningType.UnicodeBidiCharacters,
      })
    }
```
