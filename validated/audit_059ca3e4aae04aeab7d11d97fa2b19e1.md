### Title
Bidirectional-control-character injection in worktree/branch names is not filtered before rendering, enabling worktree-list spoofing - (File: `app/src/ui/lib/highlight-text.tsx`)

### Summary
`HighlightText` renders arbitrary text character-by-character into DOM `<span>`/`<mark>` nodes with no sanitization beyond React's default HTML-entity escaping. Unicode bidirectional-control characters (e.g. `U+202E` RIGHT-TO-LEFT OVERRIDE, `U+2066`–`U+2069` isolates) are not HTML metacharacters, so React passes them through untouched, and the browser's bidi algorithm then visually re-orders the text. `WorktreeListItem` feeds the worktree's display name straight into `HighlightText` [1](#0-0) , and that name is only `Path.basename(worktree.path)` [2](#0-1) .

### Finding Description
The worktree's `path` originates from either the user typing it or from an auto-populated default name in `AddWorktreeDialog`/`RepositoryPath`. When a user right-clicks a branch and chooses "Checkout in new worktree", the dialog is pre-filled with `${repository.name}-${branch.nameWithoutRemote}` [3](#0-2) . `branch.nameWithoutRemote` for a remote-tracking branch is derived directly from a ref name fetched from a remote, which is attacker-controlled content when the remote/fork is attacker-controlled.

That name flows into `RepositoryPath.getFullPath()`, which only runs it through `safeDirectoryName`, a function that on non-Windows platforms performs no filtering at all, and on Windows only strips `<>:"|?*` and trailing whitespace — it does not strip Unicode bidi control characters [4](#0-3) [5](#0-4) . Likewise `sanitizedRefName`, used for the "create new branch" fallback name, only strips ASCII control characters (`\x00-\x20`, `\x7F`) and a short blacklist of punctuation — Unicode bidi formatting characters (which are outside the ASCII range) pass through unfiltered [6](#0-5) . `git check-ref-format` itself only rejects ASCII control bytes, so a ref name containing bidi override characters is a valid git ref.

The resulting directory is created on disk via `git worktree add` with the unfiltered name, so `git worktree list --porcelain` subsequently reports a `worktree` path containing the embedded bidi characters, which `parseWorktreePorcelainOutput` copies verbatim into `WorktreeEntry.path` [7](#0-6) . `getWorktreeDisplayName` (`Path.basename`) preserves the characters, and `WorktreeListItem` renders them unescaped through `HighlightText`, and also as an unsanitized tooltip/aria-label string [8](#0-7) [9](#0-8) .

Because the DOM text content is affected only by the browser's Unicode bidi rendering algorithm and not by any escaping, two worktree entries whose underlying path strings differ only in the placement of bidi override/isolate characters can be made to visually render as identical (or misleadingly swapped) text in the worktree switcher list, even though they point to different directories/branches on disk.

### Impact Explanation
If a user is tricked into believing they are viewing/switching to worktree A's entry while it is actually worktree B (pointing at a different branch), subsequent commits or pushes would silently land on the unintended branch — this matches the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
Exploitation requires several conditions to line up: the attacker must control a remote/fork branch name containing bidi control characters (git and GitHub generally permit non-ASCII bytes in ref names), the victim must use the "Checkout in new worktree" flow and accept the auto-populated name without editing/noticing the invisible characters, and the victim must later fail to notice the spoofed entry when switching/committing. This is a real, reachable rendering-sanitization gap, but the multi-step, opt-in nature of worktree creation lowers the likelihood compared to a fully passive drive-by attack. It is also the same general class of unsanitized-text-rendering issue present in other components (`PathText`/`PathLabel` also feed raw paths into `HighlightText`), not a defect unique to the worktree feature.

### Recommendation
Strip or visually neutralize Unicode bidirectional-control and other zero-width/format characters (`U+200B–U+200F`, `U+202A–U+202E`, `U+2066–U+2069`, etc.) in `sanitizedRefName`, `safeDirectoryName`, and/or `HighlightText`/`PathText` before rendering, or wrap rendered path/branch segments in a fixed `dir="ltr"`/`unicode-bidi: isolate` (with control characters replaced) so no embedded characters can escape the enclosing bidi context.

### Proof of Concept
1. On a remote/fork under attacker control, create a branch whose name embeds `U+202E` (RLO) such that when combined with a suffix it renders as an unrelated, benign-looking name (classic Trojan-Source style construction), e.g. `feature-\u202Emalicious-drofpu`.
2. Victim fetches the branch and uses Desktop's branch dropdown "Checkout \<branch\> in new worktree" action, which pre-fills the worktree name from `branch.nameWithoutRemote` [3](#0-2) .
3. Victim accepts the default and creates the worktree; the directory is created on disk with the embedded bidi character intact (`safeDirectoryName` does not strip it) [4](#0-3) .
4. Repeat with a second, differently-named branch crafted so its bidi-reordered rendering is visually indistinguishable from the first.
5. Open the worktree dropdown/list; both entries render through `HighlightText` [10](#0-9)  and appear identical, despite pointing at different branches/directories.

### Citations

**File:** app/src/ui/worktrees/worktree-list-item.tsx (L24-42)
```typescript
    const name = getWorktreeDisplayName(worktree)
    const description = getWorktreeDescription(worktree)
    const icon = isCurrentWorktree ? octicons.check : octicons.fileDirectory
    const className = classNames('worktrees-list-item', {
      'current-worktree': isCurrentWorktree,
    })

    return (
      <div className={className}>
        <Octicon className="icon" symbol={icon} />
        <TooltippedContent
          className="name"
          tooltip={name}
          onlyWhenOverflowed={true}
          tagName="div"
          disabled={enableAccessibleListToolTips()}
        >
          <HighlightText text={name} highlight={matches.title} />
        </TooltippedContent>
```

**File:** app/src/models/worktree.ts (L17-20)
```typescript
/** The display name for a worktree (the basename of its path). */
export function getWorktreeDisplayName(worktree: WorktreeEntry): string {
  return Path.basename(worktree.path)
}
```

**File:** app/src/ui/toolbar/branch-dropdown.tsx (L414-422)
```typescript
  private onCheckoutInNewWorktree = (branch: Branch) => {
    this.props.dispatcher.closeFoldout(FoldoutType.Branch)
    this.props.dispatcher.showPopup({
      type: PopupType.AddWorktree,
      repository: this.props.repository,
      initialBranchName: branch.name,
      initialWorktreeName: `${this.props.repository.name}-${branch.nameWithoutRemote}`,
    })
  }
```

**File:** app/src/ui/lib/repository-path.tsx (L23-25)
```typescript
const safeDirectoryName = (name: string) => {
  return __WIN32__ ? name.replace(/[<>:"|?*]/g, '-').replace(/\s+$/, '') : name
}
```

**File:** app/src/ui/lib/repository-path.tsx (L123-129)
```typescript
  private getFullPath(): string | null {
    const { name, path } = this.state
    if (path === null || path.length === 0 || name.trim().length === 0) {
      return null
    }
    return Path.join(path, safeDirectoryName(name))
  }
```

**File:** app/src/lib/sanitize-ref-name.ts (L1-11)
```typescript
// See https://www.kernel.org/pub/software/scm/git/docs/git-check-ref-format.html
// ASCII Control chars and space, DEL, ~ ^ : ? * [ \
// | " < and > is technically a valid refname but not on Windows
// the magic sequence @{, consecutive dots, leading and trailing dot, ref ending in .lock
const invalidCharacterRegex =
  /[\x00-\x20\x7F~^:?*\[\\|""<>]+|@{|\.\.+|^\.|\.$|\.lock$|\/$/g

/** Sanitize a proposed reference name by replacing illegal characters. */
export function sanitizedRefName(name: string): string {
  return name.replace(invalidCharacterRegex, '-').replace(/^[-\+]*/g, '')
}
```

**File:** app/src/lib/git/worktree.ts (L28-37)
```typescript
    for (const line of lines) {
      if (line.startsWith('worktree ')) {
        // Git for Windows will output paths using forward slashes, i.e.
        // c:/Users/niik/... but repositories added in Desktop always pass
        // through getRepositoryType which uses path.resolve to deduce the
        // absolute top level directory and that will normalize paths as well
        // so by normalizing here we can be more confident about comparing paths
        path = Path.normalize(line.substring('worktree '.length))
      } else if (line.startsWith('HEAD ')) {
        head = line.substring('HEAD '.length)
```

**File:** app/src/ui/worktrees/worktree-list.tsx (L102-107)
```typescript
  private getItemAriaLabel = (item: IWorktreeListItem) => {
    const { worktree } = item
    return `${getWorktreeDisplayName(worktree)}, ${getWorktreeDescription(
      worktree
    )}`
  }
```
