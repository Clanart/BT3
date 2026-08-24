Based on the local evidence gathered, the strongest and most concrete analog is not in the areas that map most literally to "arbitrary amount specification" (clone path traversal, submodule protocol, deep-link `filepath` handling) — all of those are explicitly guarded in this codebase (`isClonePathSensitive`, `sanitizeCloneName`, `resolveWithin` + `isAbsolute` checks in `dispatcher.ts`). The remaining gap is in the markdown rendering pipeline, where attacker-controlled repository content (a GitHub API object: PR/issue/commit body) is trusted and written into the DOM via `innerHTML` **after** the DOMPurify sanitization pass has already run.

### Title
Post-sanitization unescaped HTML injection via attacker-controlled commit-link path in markdown rendering - (File: app/src/lib/markdown-filters/commit-mention-link-filter.ts)

### Summary
`SandboxedMarkdown` sanitizes rendered markdown with DOMPurify and loads it into an iframe, then runs a pipeline of `INodeFilter`s (`applyFilters`) against the already-sanitized DOM [1](#0-0) . One of these filters, `CommitMentionLinkFilter`, extracts `owner`, `name`, and a trailing `filePath`/query string directly from the **text of an attacker-controlled link** (a PR/issue/commit body can contain any URL whose visible text equals its href) and concatenates them, unescaped, into an HTML string that is then assigned to `newNode.innerHTML` [2](#0-1) [3](#0-2) .

### Finding Description
The broken invariant is: content coming from an untrusted, attacker-controlled GitHub API object (a pull request/issue/commit description that is rendered to HTML) is assumed to have already been fully sanitized by the time it reaches downstream DOM-manipulation code — but `CommitMentionLinkFilter.filter` reintroduces raw markup **after** DOMPurify has already executed, because the filter pipeline runs post-sanitization on the loaded document [4](#0-3) .

The `owner`, `name`, and `filePath`/query (`url.search`) values are taken straight from `url.pathname`/`url.search` of the attacker's link text with no HTML-escaping [5](#0-4) , then interpolated into a template string that is assigned via `innerHTML`:
```
return `${ownerRepo}<tt>${trimmedSha}</tt>${filePath ?? ''}`
``` [6](#0-5) 
This is the exact centralized-trust pattern from the external report translated to Desktop: a value supplied by an untrusted party (here, `filePath`/`owner`/`name` derived from attacker text) is used directly by a "privileged" sink (`innerHTML`, which can create arbitrary elements/attributes) without any additional validation, and the existing guard (DOMPurify) does not run again after this mutation — the filter pipe executes once, after purification, with no re-sanitization step [7](#0-6) .

### Impact Explanation
Because the rendering happens inside a sandboxed iframe (`sandbox="allow-same-origin"`, no `allow-scripts`) [8](#0-7) , direct `<script>`/event-handler execution is very likely blocked by Electron's sandbox attribute. However, the injected raw HTML still corrupts what the user actually sees and can carry non-script markup (e.g., crafted anchors, `<base>`-like elements, image/link elements that trigger outbound network requests) that DOMPurify would otherwise have stripped. This constitutes silent corruption of rendered, trusted-looking content (commit/PR references) with attacker-controlled markup that bypassed the app's sanitizer, and is the type of vector that composes badly with any future relaxation of the sandbox attribute or other filters in the same post-purification pipeline.

I was not able to fully verify, from the indexed code alone, whether any other component in this pipeline re-sanitizes the DOM after `applyFilters` runs, or whether the Electron `sandbox` attribute in this build additionally restricts network-triggering elements — this would need to be confirmed with a running build/DOM inspection.

### Likelihood Explanation
Any attacker who can get a PR title/description, issue body, or commit message merged/opened against a repository the victim opens in Desktop can trigger this — no local access, admin rights, or social engineering beyond normal open-source collaboration is required, satisfying the "attacker controls a GitHub API object" criterion. The trigger condition (an anchor whose `href` equals its own `innerText` and matches the commit/compare/pull-commit URL shape) is trivial to construct.

### Recommendation
Escape `owner`, `name`, and `filePath`/`url.search` before interpolating them into the HTML template in `getCommitMentionRef`, or build the replacement using DOM APIs (`textContent`/`createElement` + `setAttribute`) instead of `innerHTML` string concatenation, matching the safer pattern already used in `CommitMentionFilter.createCommitMentionLinkElement` for the `ref`/ innerHTML but which is itself only marginally safer since `ref` is regex-constrained to hex characters. More generally, run DOMPurify.sanitize again (or use a strict per-filter escaping) after any node filter that injects raw HTML derived from untrusted repository content.

### Proof of Concept
1. Open a pull request/issue/commit whose description contains a link whose text is:
   `https://github.com/<owner>/<repo>/commit/1234567/"><svg onload=alert(document.domain)>`
   formatted so `href === innerText` (this happens automatically for auto-linked bare URLs in markdown).
2. `CommitMentionLinkFilter.filter` matches this against `commitMentionUrl`, extracts `filepathToAppend` = `"><svg onload=alert(document.domain)>` from `getRefFromCommitPath` (no HTML-escaping is applied to `pathFragment`) [9](#0-8) .
3. `getCommitMentionRef` embeds this unescaped string into the returned HTML string and it is assigned to `newNode.innerHTML` [10](#0-9) , producing DOM nodes never seen by DOMPurify.
4. Whether this achieves script execution depends on the iframe sandbox flags at runtime (unverified from index alone); at minimum it demonstrates a sanitizer bypass allowing arbitrary markup injection into rendered PR/commit/issue content.

### Citations

**File:** app/src/ui/lib/sandboxed-markdown.tsx (L320-340)
```typescript
  private onDocumentDOMContentLoaded = (doc: Document) => {
    if (this.currentDocument !== doc) {
      return
    }

    this.refreshHeight()

    Array.from(doc.querySelectorAll('img')).forEach(img =>
      img.addEventListener('load', this.refreshHeight)
    )

    Array.from(doc.querySelectorAll('details')).forEach(detail =>
      detail.addEventListener('toggle', this.refreshHeight)
    )

    this.applyFilters(doc)
    this.setupLinkInterceptor(doc)
    this.setupTooltips(doc)

    this.props.onMarkdownParsed?.()
  }
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

**File:** app/src/ui/lib/sandboxed-markdown.tsx (L384-399)
```typescript
  public render() {
    const { tooltipElements, tooltipOffset } = this.state

    return (
      <div
        className="sandboxed-markdown-iframe-container"
        ref={this.frameContainingDivRef}
      >
        <iframe
          title="sandboxed-markdown-component"
          className="sandboxed-markdown-component"
          sandbox="allow-same-origin"
          ref={this.onFrameRef}
          onLoad={this.refreshHeight}
          aria-label={this.props.ariaLabel}
        />
```

**File:** app/src/lib/markdown-filters/commit-mention-link-filter.ts (L125-165)
```typescript
  public async filter(node: Node): Promise<ReadonlyArray<Node> | null> {
    const newNode = node.cloneNode(true)
    const { textContent: text } = newNode
    if (!isElement(newNode, 'a') || text === null) {
      return null
    }

    const url = new URL(text)
    const [, owner, name] = url.pathname.split('/', 3)
    if (owner === undefined || name === undefined) {
      return null
    }
    const slashes = 3
    const path = url.pathname.substring(owner.length + name.length + slashes)

    let ref, filepathToAppend

    const commitComparePathMatch =
      this.getRefFromCommitPath(path) ?? this.getRefFromComparePath(path)
    if (commitComparePathMatch !== null) {
      ;({ ref, filepathToAppend } = commitComparePathMatch)

      filepathToAppend =
        filepathToAppend !== undefined
          ? filepathToAppend + url.search
          : url.search
    } else {
      ref = this.getRefFromPullPath(path)
    }

    if (ref === null || ref === undefined) {
      return null
    }

    newNode.innerHTML = this.getCommitMentionRef(
      owner,
      name,
      ref,
      filepathToAppend
    )
    return [newNode]
```

**File:** app/src/lib/markdown-filters/commit-mention-link-filter.ts (L168-198)
```typescript
  private getRefFromCommitPath(path: string) {
    const match = path.match(this.commitPath)
    if (match === null || match.groups === undefined) {
      return null
    }

    const { pathFragment } = match.groups
    const slashIndex = pathFragment.indexOf('/')
    const possibleSha =
      slashIndex >= 0 ? pathFragment.slice(0, slashIndex) : pathFragment
    const filepathToAppend =
      slashIndex >= 0 ? pathFragment.slice(slashIndex) : undefined

    if (possibleSha === undefined) {
      return null
    }
    const [sha, format] = possibleSha.split('.')

    if (
      sha === undefined ||
      this.isReservedCommitActionPath(filepathToAppend) ||
      format !== undefined
    ) {
      return null
    }

    return {
      ref: this.trimCommitSha(sha),
      filepathToAppend,
    }
  }
```

**File:** app/src/lib/markdown-filters/commit-mention-link-filter.ts (L269-284)
```typescript
  /**
   * Creates commit sha references
   */
  private getCommitMentionRef(
    owner: string,
    name: string,
    shaRef: string,
    filePath?: string
  ) {
    const ownerRepo =
      owner !== this.repository.owner.login || name !== this.repository.name
        ? `${owner}/${name}@`
        : ''
    const trimmedSha = this.trimCommitSha(shaRef)
    return `${ownerRepo}<tt>${trimmedSha}</tt>${filePath ?? ''}`
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
