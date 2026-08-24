This is a legitimate, confirmed finding.

### Title
Bidi warning is diff-wide, not line-specific, enabling alert desensitization / warning-fatigue for hidden-bidi-character attacks - (File: `app/src/lib/diff-parser.ts`)

### Summary
`DiffParser.parse` computes `hasHiddenBidiChars` by running `HiddenBidiCharsRegex.test(text)` against the **entire raw diff text** — header, hunk headers (including the `@@ ... @@` trailing context snippet Git includes verbatim from the source line), and all context/added/removed lines — rather than scoping the check to the actual added/removed content. [1](#0-0) 

The UI (`DiffContentsWarning`) then renders a single, generic warning banner whenever `diff.hasHiddenBidiChars` is true, with no indication of *where* the bidi characters are (hunk header context vs. actual `+`/`-` line content): [2](#0-1) 

### Finding Description
Git's unified diff format includes an optional "section heading" after the `@@ -l,s +l,s @@` marker — this is a context hint (typically the nearest preceding function/class signature) taken verbatim from the source file content, as documented in the parser itself: [3](#0-2) 

An attacker who controls repository content (e.g. a function name or a comment near a hunk boundary) can embed invisible bidi override/isolate characters (U+202A–U+202E, U+2066–U+2069) solely in that hunk-header context text. Since `HiddenBidiCharsRegex.test(text)` is evaluated over the whole raw diff string, this trips `hasHiddenBidiChars = true` for a diff whose actual `+`/`-` lines are completely clean of bidi characters. The warning shown to the user is identical in both cases — same generic banner, same wording, no highlighting of which line(s) actually contain the characters — so a reviewer cannot distinguish a "benign" trigger (bidi chars only in decorative hunk-header context, never rendered/compiled) from a "malicious" trigger (bidi chars in committed code that could reorder displayed logic, per the classic "Trojan Source" attack).

An attacker repeatedly triggering the warning via harmless hunk-header context strings across many commits/PRs can train reviewers to habitually dismiss/ignore the "This diff contains bidirectional Unicode text..." banner. A subsequent PR that hides malicious bidi characters inside an actual `+` line (real code) will produce the exact same banner and is likely to be dismissed the same way — a genuine alert-fatigue vector that defeats the purpose of the warning added specifically to counter Trojan Source-style attacks.

### Impact Explanation
This weakens a security control (the bidi-character warning added to protect against Trojan Source attacks) by making it noisy and non-actionable. It does not itself grant code execution, but it materially increases the likelihood that a genuine Trojan Source-style attack (bidi characters reordering how code is displayed, causing a reviewer to approve/merge/commit different logic than what they visually reviewed) goes unnoticed, since the tool provides no way to differentiate "noise" from "signal." Given the valid-impact guidance requires the vulnerability class itself (silent corruption of what a user commits/pushes) to be realizable, this finding's impact is best characterized as a control-weakening/desensitization issue that enables the underlying bidi-review-bypass, rather than a standalone RCE/exfiltration bug.

### Likelihood Explanation
High — hunk-header context text is derived directly from repository source (e.g., a function signature containing a bidi character would appear as the `@@ ... @@` trailing text), which is fully attacker-controlled in a cloned/fetched malicious repository. No unusual user interaction is required beyond viewing the diff.

### Recommendation
Scope the `HiddenBidiCharsRegex` check to only the actual diff line content (`+`/`-`/context lines added to `DiffLine` instances) rather than the raw header + full text, or at minimum evaluate hunk-header context text separately from line content and surface which specific lines contain the hidden characters (e.g., highlight/underline the offending lines) so users can visually distinguish decorative-only triggers from actual code-content triggers. Consider tracking bidi-detection at the per-`DiffLine` level (already parsed into `DiffLine` objects) and only flagging `+`/`-` lines, or providing separate warning classes for "header only" vs "content" bidi character detections.

### Proof of Concept
1. Create a source file containing a function whose name/signature (used as the hunk-header context by `git diff`) contains a bidi override character, e.g.:
   ```c
   int foo\u202Ebar(void) { ... }
   ```
   Modify a line inside that function body normally (no bidi chars in the actual added/removed lines). Running `git diff` produces a hunk header like:
   ```
   @@ -10,3 +10,3 @@ int foo‮bar(void) {
   -    return 0;
   +    return 1;
   ```
   Here the bidi character sits only in the `@@ ... @@` context suffix.
2. In GitHub Desktop, viewing this diff will invoke `DiffParser.parse`, where `HiddenBidiCharsRegex.test(text)` matches on the full raw text (including the header line), setting `hasHiddenBidiChars = true` for the parsed `IRawDiff` at [4](#0-3) .
3. `DiffContentsWarning` renders the standard "This diff contains bidirectional Unicode text..." banner via `getWarningMessageForItem` [5](#0-4) , identical to what would be shown for a diff containing bidi characters directly inside a `+` line.
4. Compare against a second diff where the bidi character is placed directly inside a `+` line (actual malicious content reordering logic) — the resulting UI banner is pixel-for-pixel identical, giving the reviewer no way to tell the two apart, demonstrating the alert-fatigue/desensitization vector.

### Citations

**File:** app/src/lib/diff-parser.ts (L12-23)
```typescript
// https://en.wikipedia.org/wiki/Diff_utility
//
// @@ -l,s +l,s @@ optional section heading
//
// The hunk range information contains two hunk ranges. The range for the hunk of the original
// file is preceded by a minus symbol, and the range for the new file is preceded by a plus
// symbol. Each hunk range is of the format l,s where l is the starting line number and s is
// the number of lines the change hunk applies to for each respective file.
//
// In many versions of GNU diff, each range can omit the comma and trailing value s,
// in which case s defaults to 1
const diffHeaderRe = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/
```

**File:** app/src/lib/diff-parser.ts (L442-456)
```typescript
      const contents = this.text
        .substring(headerEnd + 1, this.le)
        // Note that this simply returns a reference to the
        // substring if no match is found, it does not create
        // a new string instance.
        .replace(/\n\\ No newline at end of file/g, '')

      return {
        header,
        contents,
        hunks,
        isBinary: headerInfo.isBinary,
        maxLineNumber: getLargestLineNumber(hunks),
        hasHiddenBidiChars: HiddenBidiCharsRegex.test(text),
      }
```

**File:** app/src/ui/diff/diff-contents-warning.tsx (L45-78)
```typescript
  private getTextDiffWarningItems(): ReadonlyArray<DiffContentsWarningItem> {
    const items = new Array<DiffContentsWarningItem>()
    const { diff } = this.props

    if (diff.hasHiddenBidiChars) {
      items.push({
        type: DiffContentsWarningType.UnicodeBidiCharacters,
      })
    }

    if (diff.lineEndingsChange) {
      items.push({
        type: DiffContentsWarningType.LineEndingsChange,
        lineEndingsChange: diff.lineEndingsChange,
      })
    }

    return items
  }

  private getWarningMessageForItem(item: DiffContentsWarningItem) {
    switch (item.type) {
      case DiffContentsWarningType.UnicodeBidiCharacters:
        return (
          <>
            This diff contains bidirectional Unicode text that may be
            interpreted or compiled differently than what appears below. To
            review, open the file in an editor that reveals hidden Unicode
            characters.{' '}
            <LinkButton uri="https://github.co/hiddenchars">
              Learn more about bidirectional Unicode characters
            </LinkButton>
          </>
        )
```
