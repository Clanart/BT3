## Title
Unbounded `ICON` token boundary scan in `ActionsLogParser.parseLines()` corrupts line/node indexing when parsing attacker-controlled GitHub Actions log output - (File: `app/src/lib/actions-log-parser/action-log-parser.ts`)

## Summary
Like the ENS `RecordParser.readKeyValue()` bug, this is a delimiter search that is logically supposed to be bounded (to the current log line) but is implemented against the wrong range (the entire multi-line `content` buffer), and whose "not found" fallback does not clamp to the intended boundary. This causes the parser's line/index bookkeeping to desynchronize when fed adversarial input — here, GitHub Actions log text, which is attacker-influenced content (any workflow step can `echo` arbitrary text, including `##[...]` pipeline commands) that Desktop fetches from the GitHub API and renders in the Checks/Actions log viewer.

## Finding Description
`ActionsLogParser.parseLines()` walks `content` (the entire raw, multi-line log text with embedded `\n` characters, produced by joining all lines in `updateLineMetaData()`) character by character. When it detects the special `##[icon]` command, it searches for the terminating space character: [1](#0-0) 

The search loop is bounded by `content.length` — the length of the *entire log*, not the length/end of the *current line*. Every other command-scanning code path in this parser treats `\n` (`newLineChar`) as the natural line boundary and resets state (`lineStartIndex`, `resetPlain()`, `resetPending()`, `resetCommandVar()`) whenever a newline is hit: [2](#0-1) 

But the `ICON` branch does not stop at `\n`; it will scan straight through embedded newlines looking for a space. Two failure modes mirror the `RecordParser` bug exactly:

- If no space exists before the true end of the buffer, `endIndex` never advances past its initial value (`startIndex`), so the node is degenerate (zero-length) instead of being clamped to the correct boundary (the end of the current line, analogous to `offset+len` in the ENS bug).
- If a space *does* exist, but only many lines later (which an attacker fully controls, since this is workflow-emitted text), `endIndex` will land inside unrelated, later log content. `index = endIndex + 1; lineStartIndex = index` then jumps the outer parsing cursor forward across those intervening lines/newlines without processing them normally, silently skipping line-boundary bookkeeping (`lines.push(...)`, `lineStartIndex` resets, `pendingLastNode` handling) for everything it steps over — exactly the kind of "read past the intended boundary and pull in the next record's data" corruption described in the ENS finding, just applied to the log line index and node graph rather than to a byte-offset return value.

## Impact Explanation
The parser's own comment states it "escapes content to prevent XSS" as part of converting log content to HTML: [3](#0-2)  — the safety of that escaping/highlighting pipeline depends on each node's `start`/`end`/`lineIndex` correctly bounding the characters it is responsible for. An attacker who controls a workflow's log output (e.g., via a malicious CI job in a repo the user has connected to Desktop's Checks feature) can craft a `##[icon]` command engineered to make the boundary-search skip across line boundaries it shouldn't, misattributing raw log text to the wrong line/node. At minimum this causes silent corruption of what's displayed to the user (missing or merged lines, incorrect timestamps/line numbers via `this.logLineNumbers[lineIndex]` and `this.timestamps[lineIndex]`), and depending on how far downstream code trusts node boundaries to have already excluded command syntax, it raises risk of rendering content that was not meant to reach the HTML/text pipeline as plain content. I was not able to fully trace every downstream consumer of `node.start`/`node.end` in this session to conclusively confirm an HTML-injection outcome; that would require deeper tracing best done in a full Devin session with the actual GitHub Actions checks UI code paths.

## Likelihood Explanation
Likelihood is moderate: it requires a user to view Actions/Checks logs for a workflow run whose log output was crafted by an untrusted party (a plausible scenario for public repos with external contributors, forks, or reused/compromised Actions), but does not require any local access, credentials, or unusual user steps beyond normal use of Desktop's checks-log viewing feature.

## Recommendation
Bound the `ICON` terminator search to the current line, mirroring how the rest of the state machine already resets on `newLineChar`:
- Search only up to the next `\n` (or `content.indexOf('\n', startIndex)`, defaulting to `content.length` only if there truly is no more content) rather than unconditionally to `content.length`.
- If no space is found within that line-bounded range, clamp `endIndex` to the end of the current line (not to `startIndex`), consistent with the ENS fix of clamping the fallback to the query range rather than the full buffer length.

## Proof of Concept
Conceptually, following the same PoC structure as the ENS report:
1. Supply `rawLogData` to `ActionsLogParser` containing a line with `##[icon]` immediately followed by a newline and then, several lines later, a line that happens to contain a space character before any other `##[icon]`/`##[...]` command closes.
2. Because the boundary search in the `ICON` branch scans through `content` (not the current line) for the next space, `endIndex` will resolve to an offset inside a later, unrelated line.
3. `index = endIndex + 1; lineStartIndex = index` then causes the main `parseLines` loop to jump forward past several real newlines without processing them, producing incorrect `lineIndex` assignments, and/or dropped/merged log lines in `getParsedLogLinesTemplateData()`.

Because I could not execute code in this environment, this PoC is derived purely from static analysis of the control flow in `action-log-parser.ts`; a background Devin session with the test harness (`app/test/unit` scaffolding for `actions-log-parser`) would be needed to construct and run an executable reproduction and confirm the exact rendered-output consequence.

### Citations

**File:** app/src/lib/actions-log-parser/action-log-parser.ts (L168-174)
```typescript
  /**
   * Converts the content to HTML with appropriate styles, escapes content to prevent XSS
   *
   * @param content
   * @param lineNumber
   */
  private parse(content: string): IParsedContent[] {
```

**File:** app/src/lib/actions-log-parser/action-log-parser.ts (L253-283)
```typescript
  /**
   * Parses the content into lines with nodes
   *
   * @param content content to parse
   */
  private parseLines(content: string): ILine[] {
    // lines we return
    const lines: ILine[] = []
    // accumulated nodes for a particular line
    let nodes: IParseNode[] = []

    // start of a particular line
    let lineStartIndex = 0
    // start of plain node content
    let plainNodeStart = unsetValue

    // tells to consider the default logic where we check for plain text etc.,
    let considerDefaultLogic = true

    // stores the command, to match one of the 'supportedCommands'
    let currentCommand = ''
    // helps in finding commands in our format "##[command]" or "[command]"
    let commandSeeker = ''

    // when line ends, this tells if there's any pending node
    let pendingLastNode: number = unsetValue

    const resetCommandVar = () => {
      commandSeeker = ''
      currentCommand = ''
    }
```

**File:** app/src/lib/actions-log-parser/action-log-parser.ts (L430-451)
```typescript
      } else if (char === commandEnd) {
        if (currentCommand === ICON) {
          const startIndex = index + 1
          let endIndex = startIndex
          for (let i = startIndex; i < content.length; i++) {
            const iconChar = content[i]
            if (iconChar === ' ') {
              endIndex = i
              break
            }
          }
          nodes.push({
            type: NodeType.Icon,
            lineIndex: lines.length,
            start: startIndex,
            end: endIndex,
            index: nodeIndex++,
          })
          // jump to post Icon content
          index = endIndex + 1
          lineStartIndex = index
          continue
```
