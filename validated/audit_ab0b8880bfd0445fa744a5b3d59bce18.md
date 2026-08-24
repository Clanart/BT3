### Title
Unhandled exception in `CommitMentionLinkFilter.getRefFromComparePath` from attacker-controlled compare URL causes crash while rendering PR/issue/commit markdown - ([File: app/src/lib/markdown-filters/commit-mention-link-filter.ts])

### Summary
`CommitMentionLinkFilter` renders GitHub `compare/...` links found in commit messages, PR descriptions, PR comments, and issue comments into a shortened SHA reference. Its `getRefFromComparePath` method assumes a compare URL always contains the `A...B` two-part range separator, but the underlying regex `comparePath` accepts any non-empty string after `compare/`. When only one side is present, `range.split('...')` yields a one-element array and the code unconditionally dereferences the (non-existent) second element, throwing a `TypeError`. This mirrors the Sui `VecPopBack` bug: code assumes an operand/array has N elements when it may only have N-1.

### Finding Description
The regex used to detect a compare path is deliberately permissive: [1](#0-0) 

`getRefFromComparePath` matches this regex, checks only that the split result isn't *more* than 2 parts, and then immediately indexes into `shas[1]` without checking it exists: [2](#0-1) 

If `range` does not contain the `...` delimiter (e.g. `compare/abc123` or `compare/somepath`), `range.split('...')` returns `['abc123']`, `shas.length` is `1` (passes the `> 2` check), and `shas[1]` is `undefined`. The next line, `shas[1].indexOf('/')`, throws `TypeError: Cannot read properties of undefined (reading 'indexOf')` — the exact "index doesn't exist" class of bug described in the report (`args[1]` accessed when `VecPopBack` only has `args[0]`).

This filter is wired into the app's central markdown rendering pipeline for content pulled from the GitHub API, alongside issue/mention/commit filters: [3](#0-2) 

The `MarkdownContext` type confirms this pipeline runs over untrusted, attacker-influenceable content types: [4](#0-3) 

The filter only accepts anchor elements whose `href` matches `commitMentionUrl` and whose visible text equals the href (auto-linked raw URLs from markdown), so any PR description, PR/issue comment, or commit body containing the plain-text URL `https://github.com/<owner>/<repo>/compare/<anything>` (without `...`) qualifies: [5](#0-4) 

### Impact Explanation
This is an unprivileged, attacker-controlled content path: anyone who can open a PR, leave a PR/issue comment, or push a commit whose message contains a bare `github.com/<owner>/<repo>/compare/<no-triple-dot>` URL can trigger this. `filter()` is `async` and is awaited by the markdown rendering pipeline in `sandboxed-markdown.tsx`; an uncaught `TypeError` here will propagate and fail rendering of that content in the renderer (denial of that view / broken rendering path), and depending on how the pipeline's caller handles rejected promises, it may surface as an unhandled promise rejection in the renderer process. It does not by itself grant code execution or credential exfiltration, but it is a genuine input-validation/robustness bug reachable purely by viewing GitHub content inside Desktop (PR view, commit view, issue/PR comments) — the same bug class and reachability profile as the seed report (crash during content rendering from untrusted input).

### Likelihood Explanation
High likelihood of occurrence: no exotic conditions are needed, only a plain-text URL of the form `https://github.com/<owner>/<repo>/compare/<single-token>` appearing as an auto-linked raw URL in any commit message, PR body, or PR/issue comment — trivially craftable by any GitHub user with write or even fork/PR access to any public repo the victim views in Desktop.

### Recommendation
In `getRefFromComparePath`, validate that the split actually produced two non-empty parts before use, e.g. return `null` when `shas.length !== 2` (instead of `> 2`) or explicitly guard `shas[1] === undefined` and bail out, consistent with the defensive `undefined` checks already used in `getRefFromCommitPath`.

### Proof of Concept
1. In any repository the Desktop user has open, create a PR or issue comment (or a commit whose message is later shown in Desktop's commit-summary view) containing the raw autolinked text:
   `https://github.com/<owner>/<repo>/compare/deadbeef`
   (no `...`, so it does not represent a valid two-ref compare link).
2. Have the victim view that PR/issue/commit in GitHub Desktop, which pipes the markdown through `buildCustomMarkDownNodeFilterPipe`, invoking `CommitMentionLinkFilter.filter()` → `getRefFromComparePath()`.
3. `range.split('...')` returns `['deadbeef']`; `shas.length` (1) passes the `> 2` check; `shas[1].indexOf('/')` throws `TypeError: Cannot read properties of undefined (reading 'indexOf')`, breaking rendering of that content.

### Citations

**File:** app/src/lib/markdown-filters/commit-mention-link-filter.ts (L50-55)
```typescript
  /**
   * A regexp that searches for a url path pattern for a compare
   *
   * Example: /desktop/desktop/commit/6fd7945...6fd7945
   */
  private readonly comparePath = /^compare\/(?<range>.+)$/
```

**File:** app/src/lib/markdown-filters/commit-mention-link-filter.ts (L102-114)
```typescript
  public createFilterTreeWalker(doc: Document): TreeWalker {
    return doc.createTreeWalker(doc.body, NodeFilter.SHOW_ELEMENT, {
      acceptNode: (el: Element) => {
        return (el.parentNode !== null &&
          ['CODE', 'PRE', 'A'].includes(el.parentNode.nodeName)) ||
          !isElement(el, 'a') ||
          el.href !== el.innerText ||
          !this.commitMentionUrl.test(el.href)
          ? NodeFilter.FILTER_SKIP
          : NodeFilter.FILTER_ACCEPT
      },
    })
  }
```

**File:** app/src/lib/markdown-filters/commit-mention-link-filter.ts (L200-224)
```typescript
  private getRefFromComparePath(path: string) {
    const match = path.match(this.comparePath)
    if (match === null || match.groups === undefined) {
      return null
    }

    const { range } = match.groups

    if (/\.(diff|path)$/.test(range)) {
      return null
    }

    const shas = range.split('...')
    if (shas.length > 2) {
      return null
    }

    const slashIndex = shas[1].indexOf('/')
    const secondSha = slashIndex >= 0 ? shas[1].slice(0, slashIndex) : shas[1]

    return {
      ref: `${this.trimCommitSha(shas[0])}...${this.trimCommitSha(secondSha)}`,
      filepathToAppend: slashIndex >= 0 ? shas[1].slice(slashIndex) : undefined,
    }
  }
```

**File:** app/src/lib/markdown-filters/node-filter.ts (L77-90)
```typescript
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
```

**File:** app/src/lib/markdown-filters/node-filter.ts (L98-103)
```typescript
/** The context of which markdown resides */
export type MarkdownContext =
  | 'PullRequest'
  | 'PullRequestComment'
  | 'IssueComment'
  | 'Commit'
```
