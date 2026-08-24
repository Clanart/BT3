This confirms the bug. `caseInsensitiveEquals` returns `true` when the two strings are equal, and `compare.ts` shows the straightforward implementation `x.toLowerCase() === y.toLowerCase()`. In `TeamMentionFilter.filter`, this result is used directly as the `continue` condition: `app/src/lib/markdown-filters/team-mention-filter.ts:95-102`. That means when `org.slice(1)` **matches** `this.repository.owner.login`, the loop `continue`s and **skips** link creation, and when they **do not match**, the code falls through and calls `createLinkElement`, generating a link to `github.com/orgs/<attacker-controlled-org>/teams/<team>`. This is the exact inverse of the documented intent in the comment on line 98 ("Team references are only added when the repository owner is the org").

### Title
Inverted org-match check in `TeamMentionFilter.filter` links team mentions to orgs that do NOT own the repo - (File: app/src/lib/markdown-filters/team-mention-filter.ts)

### Summary
The `TeamMentionFilter` is meant to only turn `@org/team` text into a hyperlink when `org` matches the repository's actual owning organization, preventing arbitrary/attacker-controlled markdown from generating misleading team links. The boolean used to guard the `continue` (skip) is inverted, so the filter does the opposite: it skips linking when the org matches the repo owner and creates a link whenever the org does NOT match.

### Finding Description
`buildCustomMarkDownNodeFilterPipe` (`app/src/lib/markdown-filters/node-filter.ts:52-96`) wires `TeamMentionFilter` into the filter pipeline used by `SandboxedMarkdown.applyFilters` (`app/src/ui/lib/sandboxed-markdown.tsx:342-382`), which processes markdown/HTML rendered from repository content such as commit messages, PR bodies/comments, and issue comments (`MarkdownContext` includes `'Commit' | 'PullRequest' | 'PullRequestComment' | 'IssueComment'`), all of which are attacker-controlled if the attacker can push a commit, open a PR, or post a comment on a repo the victim views in Desktop.

Inside `filter()` (`app/src/lib/markdown-filters/team-mention-filter.ts:75-120`), for each `@org/team` regex match the code does:
```
if (
  org === undefined ||
  team === undefined ||
  // Team references are only added when the repository owner is the org...
  caseInsensitiveEquals(org.slice(1), this.repository.owner.login)
) {
  continue
}
const link = this.createLinkElement(org.slice(1), team.slice(1))
```
`caseInsensitiveEquals` returns `true` exactly when the org matches the repo owner (`app/src/lib/compare.ts:72-74`). Since a `true` result triggers `continue` (skip), a matching org is *skipped*, while a *non-matching* org falls through and reaches `createLinkElement`, generating `href = ${baseHref}/orgs/${org}/teams/${team}` (line 104-127). This is the exact opposite of the stated invariant.

### Impact Explanation
An attacker who controls repository content the victim views in GitHub Desktop (a commit description, PR body/comment, or issue comment) can insert text like `@some-other-org/team`. The renderer will turn this into a live hyperlink to `https://github.com/orgs/some-other-org/teams/team`, even though the repository is owned by an unrelated org (`my-org`). The click is intercepted by `setupLinkInterceptor` (`sandboxed-markdown.tsx:292-305`) and only requires `https?:` scheme before invoking `onMarkdownLinkClicked`, which for GitHub host links generally opens the URL in the user's default browser. This misrepresents provenance/trust of the rendered content (implying a legitimate, repo-scoped team reference when it is actually attacker-chosen), and can be used to craft convincing-looking but bogus team links pointing to arbitrary orgs/teams (including attacker-controlled names) as part of a social-engineering or spoofing setup. It does not, by itself, achieve code execution, sandbox escape, file access outside the repo, or credential exfiltration — the resulting action is opening a normal `https://github.com/orgs/.../teams/...` URL in the external browser, which is a benign, same-origin GitHub URL and requires the user to click it.

### Likelihood Explanation
Trivial to trigger — any collaborator/attacker with commit/PR/issue access to a repository the victim opens in Desktop can add `@arbitrary-org/team` text to a commit message, PR description, PR comment, or issue comment. The regex (`teamMentionRegex`) has no restriction tying `org` to the repo owner other than this (broken) check.

### Recommendation
Negate the comparison so the `continue` correctly fires when the org does NOT match the repository owner:
```ts
if (
  org === undefined ||
  team === undefined ||
  !caseInsensitiveEquals(org.slice(1), this.repository.owner.login)
) {
  continue
}
```

### Proof of Concept
1. Open a repository in GitHub Desktop owned by organization `my-org`.
2. As a collaborator, push a commit with the message body: `cc: @some-other-org/team please review`.
3. View the commit in Desktop's commit summary/description (rendered via `SandboxedMarkdown` with `repository` set and `markdownContext: 'Commit'`).
4. Observe that `@some-other-org/team` is rendered as a clickable link pointing to `https://github.com/orgs/some-other-org/teams/team`, despite the repo owner being `my-org` — the reverse of the intended behavior (which should link only when org === `my-org`, and leave `@some-other-org/team` as plain text).

Note: While the logical inversion is clearly a real bug relative to the documented invariant, its exploitable security impact is limited to link/content spoofing (a mislabeled, non-malicious `github.com` URL) rather than the higher-severity outcomes required by the bounty scope (code execution, file read/write outside repo, credential exfiltration, sandbox/IPC escape, or silent corruption of commits/pushes). Whether this rises to a valid, in-scope, and rewardable finding depends on the program's own severity bar for content-spoofing/UI-misrepresentation issues, which I cannot determine from the code alone. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** app/src/lib/markdown-filters/team-mention-filter.ts (L89-104)
```typescript
    for (const match of matches) {
      if (match.groups === undefined || match.index === undefined) {
        continue
      }

      const { org, team } = match.groups
      if (
        org === undefined ||
        team === undefined ||
        // Team references are only added when the repository owner is the org to prevent linking to a team outside the repositories org.
        caseInsensitiveEquals(org.slice(1), this.repository.owner.login)
      ) {
        continue
      }

      const link = this.createLinkElement(org.slice(1), team.slice(1))
```

**File:** app/src/lib/compare.ts (L72-74)
```typescript
export function caseInsensitiveEquals(x: string, y: string): boolean {
  return x.toLowerCase() === y.toLowerCase()
}
```

**File:** app/src/lib/markdown-filters/node-filter.ts (L52-96)
```typescript
export const buildCustomMarkDownNodeFilterPipe = memoizeOne(
  (options: ICustomMarkdownFilterOptions): ReadonlyArray<INodeFilter> => {
    const { emoji, repository, markdownContext } = options
    const filterPipe: Array<INodeFilter> = []

    if (repository !== undefined) {
      /* The CloseKeywordFilter must be applied before the IssueMentionFilter or
       * IssueLinkFilter so we can scan for plain text or pasted link issue
       * mentions in conjunction wth the keyword.
       */
      if (
        markdownContext !== undefined &&
        isIssueClosingContext(markdownContext)
      ) {
        filterPipe.push(new CloseKeywordFilter(markdownContext, repository))
      }

      filterPipe.push(
        new IssueMentionFilter(repository),
        new IssueLinkFilter(repository)
      )
    }

    filterPipe.push(new EmojiFilter(emoji))

    if (repository !== undefined) {
      filterPipe.push(
        // Note: TeamMentionFilter was placed before MentionFilter as they search
        // for similar patterns with TeamMentionFilter having a larger application.
        // @org/something vs @username. Thus, even tho the MentionFilter regex is
        // meant to prevent this, in case a username could be encapsulated in the
        // team mention like @username/something, we do the team mentions first to
        // eliminate the possibility.
        new TeamMentionFilter(repository),
        new MentionFilter(repository),
        new CommitMentionFilter(repository),
        new CommitMentionLinkFilter(repository)
      )
    }

    filterPipe.push(new VideoTagFilter(), new VideoLinkFilter())

    return filterPipe
  }
)
```

**File:** app/src/ui/lib/sandboxed-markdown.tsx (L292-305)
```typescript
  private setupLinkInterceptor(doc: Document): void {
    doc.addEventListener('click', ev => {
      if (doc.defaultView && ev.target instanceof doc.defaultView.Element) {
        const a = ev.target.closest('a')
        if (a !== null) {
          ev.preventDefault()

          if (/^https?:/.test(a.protocol)) {
            this.props.onMarkdownLinkClicked?.(a.href)
          }
        }
      }
    })
  }
```

**File:** app/src/ui/lib/sandboxed-markdown.tsx (L342-382)
```typescript
  private async applyFilters(doc: Document) {
    const { emoji, repository, markdownContext } = this.props
    const filters = buildCustomMarkDownNodeFilterPipe({
      emoji,
      repository,
      markdownContext,
    })

    for (const nodeFilter of filters) {
      let docMutated = false
      const walker = nodeFilter.createFilterTreeWalker(doc)

      let node = walker.nextNode()
      while (node !== null) {
        const replacementNodes = await nodeFilter.filter(node)

        if (this.currentDocument !== doc) {
          // Abort, the document has changed
          return
        }

        const currentNode = node
        node = walker.nextNode()

        if (replacementNodes === null) {
          continue
        }

        docMutated = true

        for (const replacementNode of replacementNodes) {
          currentNode.parentNode?.insertBefore(replacementNode, currentNode)
        }
        currentNode.parentNode?.removeChild(currentNode)
      }

      if (docMutated) {
        this.refreshHeight()
      }
    }
  }
```
