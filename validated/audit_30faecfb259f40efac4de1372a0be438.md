Based on my investigation, the path flows from `DiffHeader.render()` → `PathLabel` → `PathText`, and at no point in this chain is there any filtering, escaping, or CSS isolation (`unicode-bidi: isolate`, `dir="auto"`, etc.) applied to the raw path string before it is inserted into the DOM as text content.

### Title
Unsanitized filenames allow Unicode bidirectional/zero-width override characters to visually spoof file paths in the diff/changes UI - (File: `app/src/ui/diff/diff-header.tsx`, `app/src/ui/lib/path-label.tsx`, `app/src/ui/lib/path-text.tsx`)

### Summary
`DiffHeader.render()` passes `this.props.path` directly to `PathLabel`, which passes it directly to `PathText`, which renders it as plain text (`<span className="filename">{fileText}</span>`) with no stripping of Unicode bidirectional control characters (e.g. U+202E RIGHT-TO-LEFT OVERRIDE, U+202D, U+2066–U+2069) or zero-width characters (U+200B, U+FEFF). Because a git tree/blob path is attacker-controlled content in a cloned/fetched repository, a crafted filename can cause the browser's bidi algorithm to visually reorder or hide characters (e.g., disguise a `.exe`/`.sh` extension, or make one filename appear to be another) in the Diff header, the Changes list (`changed-file.tsx`), the History file list (`committed-file-item.tsx`), and the pull-request file list (`pull-request-files-changed.tsx`), since all of them route through the same `PathLabel`/`PathText` components.

### Finding Description
The render chain: [1](#0-0) [2](#0-1) [3](#0-2) 

`PathText` only normalizes the path (`Path.normalize`) and truncates it for width purposes; it does not filter or escape Unicode control characters: [4](#0-3) 

The final render inserts `fileText`/`directoryText` (raw substrings of the untrusted path) as React text children: [5](#0-4) 

Since React text nodes are rendered by the browser, any bidi control characters in the string are interpreted by the browser's Unicode bidirectional algorithm at render time, allowing visual reordering of the displayed filename (a classic "Trojan Source"/RLO filename spoofing technique) independent of the underlying string value.

I found sanitization elsewhere in the codebase (e.g., `sanitizeForMarkdown` in `app/src/lib/copilot-conflict-context.ts`, which strips `\r`, `\n`, and backticks before building Markdown prompts) confirming that the project is aware of and mitigates path-based injection risks in some contexts, but no equivalent stripping/isolation exists for the diff/changes file-path rendering path.

### Impact Explanation
This allows a malicious repository to make a file's displayed name/extension misleading in the Changes list, Diff header, commit history, and PR file list. A user could be tricked into believing they are staging, committing, discarding, or checking out a different file/extension than what is actually shown, matching the "silent corruption of what the user commits or pushes" impact category — the user's mental model of "what I'm committing" is corrupted by the spoofed rendering, even though the underlying `path` value used for git operations remains correct.

### Likelihood Explanation
Likelihood is moderate. Creating a git blob/tree entry with such a filename is straightforward and file systems/git itself do not reject bidi control characters in filenames, so cloning an attacker's repository is enough to trigger the rendering issue; no additional user action beyond opening/viewing the file in Desktop is required.

### Recommendation
- In `PathText`'s render (and any other place a raw path becomes visible UI text — `PathLabel`, `path-label.tsx`), either strip/escape Unicode bidirectional control characters and zero-width characters (U+200B, U+200E, U+200F, U+202A–U+202E, U+2066–U+2069, U+FEFF) before rendering, or wrap the rendered text in a CSS bidi-isolating container (`style={{ unicodeBidi: 'plaintext' }}` or `dir="auto"` with `unicode-bidi: isolate`) so the browser cannot reorder rendering beyond the isolated span.
- Consider surfacing a warning icon/tooltip (similar to the existing `diff-contents-warning.tsx`, which already references bidi characters in a different context) when a path contains such characters, so users are alerted rather than silently shown a spoofed name.

### Proof of Concept
1. Create a repository containing a file named `test\u202Etxt.exe` (i.e., `test` + U+202E + `txt.exe`) — this renders visually as `test.exe` reversed/altered depending on trailing characters, a well-known RLO trick to disguise executables as text files.
2. Clone this repository in GitHub Desktop and modify/stage the file.
3. Observe the rendered text in the Changes list / Diff header (`PathLabel` → `PathText` → `<span className="filename">`) — the DOM's `textContent` still equals the raw string with U+202E embedded, but the browser paints it with reordered/reversed characters, so the on-screen label does not match a naive left-to-right reading of the filename.
4. Compare `element.textContent` (raw, correct order) against the visually rendered order (reordered by the browser due to U+202E) to demonstrate the discrepancy between "what git will commit/checkout" and "what the user visually perceives."

### Citations

**File:** app/src/ui/diff/diff-header.tsx (L36-38)
```typescript
    return (
      <div className="header">
        <PathLabel path={this.props.path} status={this.props.status} />
```

**File:** app/src/ui/lib/path-label.tsx (L61-71)
```typescript
    } else {
      return (
        <span {...props} aria-hidden={this.props.ariaHidden}>
          <PathText
            path={this.props.path}
            matches={matches}
            availableWidth={availableWidth}
          />
        </span>
      )
    }
```

**File:** app/src/ui/lib/path-text.tsx (L230-239)
```typescript
function createState(path: string, length?: number): IPathTextState {
  const normalizedPath = Path.normalize(path)
  return {
    longestFit: 0,
    shortestNonFit: undefined,
    availableWidth: undefined,
    fullTextWidth: undefined,
    ...createPathDisplayState(normalizedPath, length),
  }
}
```

**File:** app/src/ui/lib/path-text.tsx (L341-358)
```typescript
    return (
      <div className="path-text-component" ref={this.pathElementRef}>
        <span ref={this.onPathInnerElementRef}>
          {directoryElement}
          <span className="filename">{fileText}</span>
        </span>
        {truncated && (
          <Tooltip
            target={this.pathElementRef}
            interactive={true}
            className="selectable path-text"
          >
            {tooltipText}
          </Tooltip>
        )}
      </div>
    )
  }
```
