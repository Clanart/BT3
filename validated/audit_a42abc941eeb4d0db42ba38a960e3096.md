Confirmed: `applyFilters` in `sandboxed-markdown.tsx` has no try/catch around `nodeFilter.filter(node)`, so an exception thrown inside `CommitMentionLinkFilter.filter` propagates uncaught, and this method is `await`ed inside `onDocumentDOMContentLoaded`, which is invoked on every markdown render for PRs, PR comments, issue comments, and commit descriptions — all attacker-controlled content from the GitHub API. [1](#0-0) 

### Title
Uncaught TypeError in `CommitMentionLinkFilter.getRefFromComparePath` when parsing a malformed `/compare/` link breaks markdown rendering - (File: app/src/lib/markdown-filters/commit-mention-link-filter.ts)

### Summary
`CommitMentionLinkFilter` parses anchor tags that look like GitHub commit/compare/pull-commit URLs found in rendered markdown (commit messages, PR bodies, PR/issue comments). Its `getRefFromComparePath` helper assumes a compare URL always contains a `...` separator (`sha1...sha2`), but it does not verify that assumption before dereferencing the second half of the split result, causing an uncaught `TypeError` that is never caught anywhere up the call chain, exactly mirroring the "missing field after last delimiter" flaw in the MultiversX advisory.

### Finding Description
`getRefFromComparePath` matches the URL path against `comparePath` (`^compare\/(?<range>.+)$`), which only requires *some* non-empty string after `compare/` — it does not require the `...` separator to be present. The method then does: [2](#0-1) 

`range.split('...')` only checks for `shas.length > 2` (rejecting 3+ parts) but never checks for `shas.length < 2`. If an attacker supplies a link whose path is `.../compare/<anything-without-triple-dot>` (e.g. `https://github.com/owner/repo/compare/deadbeef`), `shas` is `['deadbeef']` and `shas[1]` is `undefined`. The very next line, `shas[1].indexOf('/')`, throws `TypeError: Cannot read properties of undefined (reading 'indexOf')`.

This is invoked from `filter()`, which is `await`ed for every DOM node inside `applyFilters()` in `sandboxed-markdown.tsx`: [3](#0-2) 

There is no `try/catch` around `nodeFilter.filter(node)`, so the exception propagates up through the `async applyFilters` promise. Because `applyFilters` is invoked fire-and-forget from `onDocumentDOMContentLoaded` (`this.applyFilters(doc)` — not awaited, no `.catch()`), it results in an unhandled promise rejection at minimum, and interrupts the remaining filter pipeline for that render pass (subsequent filters in `filterPipe`, e.g. `VideoTagFilter`/`VideoLinkFilter`, never run for that document since the loop throws mid-iteration). [4](#0-3) 

The commit-mention link is only reachable when the anchor's `href` equals its inner text and matches `commitMentionUrl`, a regex requiring the GitHub host, `owner/name`, and a 7-40 char hex string; the crash occurs deeper in `getRefFromComparePath` regardless because `commitMentionUrl`'s pattern for `compare` does not enforce the `...sha` shape either — it only requires one hex sha to appear somewhere after `compare/`.

### Impact Explanation
This markdown pipeline runs on GitHub API content that is fully attacker-controlled: any pull request description, PR/issue comment, or commit message from a repository the user views in Desktop can embed a crafted `.../compare/<hex-no-dots>` link as plain text (auto-linked by the earlier `marked` parser into an `<a>` tag). Since `applyFilters` is not awaited and has no rejection handler, the failure silently truncates the markdown post-processing pipeline for that render — later filters that strip dangerous `<video>` tags (`VideoTagFilter`) or rewrite other links are skipped for nodes after the throw point, and `onMarkdownParsed` and height-refresh logic tied to filter completion never runs for that pass. This does not itself grant renderer-sandbox escape or code execution, but it silently corrupts the sanitization/normalization guarantees the filter pipeline provides for attacker-controlled content, and the unhandled rejection is a robustness/DoS-class flaw for the rendering of that content — directly analogous in class to the original "malformed input causes downstream unhandled state" bug, though the practical blast radius here is confined to the markdown filter pipeline rather than full node/consensus processing.

### Likelihood Explanation
High likelihood of triggering: crafting a GitHub PR/commit/issue comment containing a bare link like `https://github.com/<owner>/<repo>/compare/deadbeef123` requires no special privileges — any GitHub user who can open a PR/issue or push a commit to a repository that the victim views in Desktop can trigger this path as soon as Desktop renders that markdown.

### Recommendation
In `getRefFromComparePath`, validate that `range` actually contains the `...` separator and that `shas.length === 2` before indexing `shas[1]` (reject with `return null` otherwise), and wrap `nodeFilter.filter(node)` calls in `applyFilters` (`app/src/ui/lib/sandboxed-markdown.tsx`) in a try/catch so a single malformed node cannot abort the rest of the filter pipeline for the whole document.

### Proof of Concept
1. In any repository, open a PR/issue/commit whose body contains the plain-text URL: `https://github.com/<owner>/<repo>/compare/deadbeef1` (a valid hex string ≥7 chars, but no `...`).
2. View that PR/commit/comment in GitHub Desktop, which renders it via `SandboxedMarkdown`.
3. `marked` auto-links the bare URL into `<a href="...">...</a>` with matching href/text, satisfying `commitMentionUrl.test(el.href)` in `createFilterTreeWalker`.
4. `filter()` calls `getRefFromCommitPath` (fails, wrong prefix) then `getRefFromComparePath`, which matches `comparePath`, computes `shas = ['deadbeef1']`, and crashes on `shas[1].indexOf('/')`.
5. The uncaught exception propagates out of the un-awaited `applyFilters()` call, producing an unhandled promise rejection and leaving subsequent filters/steps for that document render un-applied.

### Citations

**File:** app/src/ui/lib/sandboxed-markdown.tsx (L335-339)
```typescript
    this.applyFilters(doc)
    this.setupLinkInterceptor(doc)
    this.setupTooltips(doc)

    this.props.onMarkdownParsed?.()
```

**File:** app/src/ui/lib/sandboxed-markdown.tsx (L342-381)
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
```

**File:** app/src/lib/markdown-filters/commit-mention-link-filter.ts (L212-218)
```typescript
    const shas = range.split('...')
    if (shas.length > 2) {
      return null
    }

    const slashIndex = shas[1].indexOf('/')
    const secondSha = slashIndex >= 0 ? shas[1].slice(0, slashIndex) : shas[1]
```
