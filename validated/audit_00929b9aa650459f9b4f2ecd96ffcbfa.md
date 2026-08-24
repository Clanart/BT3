### Title
HTML injection via unsanitized `innerHTML` assignment in `CommitMentionLinkFilter` bypasses DOMPurify sanitization, enabling link-spoofing in rendered markdown - (File: `app/src/lib/markdown-filters/commit-mention-link-filter.ts`)

### Summary
`CommitMentionLinkFilter.filter()` parses attacker-controlled markdown-derived anchor text (the trailing path segment and query string of a GitHub commit/compare URL that a PR/issue/commit author fully controls) and writes it back into the DOM using an unescaped `newNode.innerHTML =` assignment. This happens in `SandboxedMarkdown.applyFilters`, which runs *after* the markdown body has already been sanitized with `DOMPurify.sanitize()` [1](#0-0) . Because this post-sanitization DOM mutation step has no further sanitization pass, it reintroduces the exact class of "trusted-structure corruption via unescaped user data" that the referenced report describes for `Bio.tokenURI`'s JSON — here the corrupted structure is the sanitized HTML DOM instead of JSON.

### Finding Description
`CommitMentionLinkFilter.filter` builds the replacement markup like this: [2](#0-1) 

`filePath` (from `filepathToAppend`) is derived from the free-form suffix of the commit path or from `url.search` of the *attacker-supplied* anchor text/href: [3](#0-2) 

The only gating check before this occurs is the `commitMentionUrl` regex, which only guarantees a valid `owner/name/(commit|pull|compare)/<sha>` **prefix** — anything after that boundary (`pathFragment` in `getRefFromCommitPath`, or `url.search`) is unconstrained free text: [4](#0-3) 

The only extra filter is `isReservedCommitActionPath`, which only excludes a short list of known GitHub action-path strings and does not perform any character escaping: [5](#0-4) 

The resulting string is assigned directly to `innerHTML`: [6](#0-5) 

This filter is invoked from `SandboxedMarkdown.applyFilters`, which walks the DOM of the document that was produced from `DOMPurify.sanitize(marked(markdown))` and mutates it directly via `insertBefore`/`removeChild` — no re-sanitization occurs on the filter's output: [7](#0-6) 

An attacker who authors a commit message, PR/issue body, or PR comment (rendered anywhere `SandboxedMarkdown` is used, e.g. `app/src/ui/pull-request-quick-view.tsx`, `app/src/ui/notifications/pull-request-comment-like.tsx`, `app/src/ui/history/expandable-commit-summary.tsx` via markdown filters) can supply text like:
```
https://github.com/owner/repo/commit/1234567abcd/"><a href="https://evil.example.com">click</a>
```
formatted as a markdown auto-link where `href === innerText` (satisfied automatically by GitHub-flavored markdown autolinking of bare URLs). Because everything after the matched sha is passed through unescaped into `innerHTML`, the injected `<a href="https://evil.example.com">` becomes a real, distinct anchor element inside the sandboxed iframe's DOM, breaking out of the intended `<tt>sha</tt>` structure — exactly analogous to how the reported Bio Protocol bug breaks JSON structure with an injected `"`.

### Impact Explanation
The sandboxed iframe used for markdown rendering only sets `sandbox="allow-same-origin"` (no `allow-scripts`), so `<script>` tags and inline event handlers will not execute: [8](#0-7) 

However, the injected markup can still contain arbitrary non-script HTML — most notably a spoofed `<a href="...">` that visually looks like a legitimate GitHub commit link but actually points anywhere the attacker wants. `SandboxedMarkdown.setupLinkInterceptor` walks up from the click target via `closest('a')` and, for any element whose `protocol` is `http(s)`, forwards its `href` unmodified to `onMarkdownLinkClicked`: [9](#0-8) 

This callback is wired in call sites such as `app/src/ui/pull-request-quick-view.tsx` and `app/src/ui/notifications/pull-request-comment-like.tsx` and ultimately results in the URL being opened in the user's default browser. Because the injected anchor's `href` is fully attacker-chosen (not the sanitized GitHub commit URL the filter intended to render), a user who trusts what looks like "click to view commit 1234567" is silently redirected to an attacker-controlled URL of the attacker's choosing — a classic link-spoofing/phishing primitive delivered through a corrupted trusted structure inside the app itself, without needing local access, admin rights, or any unnatural user action beyond a single click on what appears to be a normal in-app commit-mention link.

### Likelihood Explanation
Likelihood is moderate: the attacker needs no special privilege — any GitHub user who can leave a PR/issue comment, PR description, or commit message that Desktop later renders can trigger this, since Desktop's `SandboxedMarkdown` renders commit/PR/comment bodies from arbitrary repositories/contributors. The prerequisite (bare autolink URL with `href === innerText`, matching the `commit|compare` path pattern) is trivially satisfiable using standard GFM autolinking behavior, and the `isReservedCommitActionPath` denylist does not block generic HTML payloads in the trailing path/query.

### Recommendation
Do not build the replacement node contents as a raw HTML string. Either:
- Construct the replacement using safe DOM APIs (`document.createElement('tt')`, `.textContent = trimmedSha`, and append `filePath` as a text node rather than concatenating into an HTML string), or
- HTML-escape `owner`, `name`, and `filePath` before interpolating them into the `innerHTML` string, and re-run `DOMPurify.sanitize()` (or an equivalent allow-list sanitizer) on any HTML produced by node filters before insertion into the live document, consistent with the sanitization already applied to the initial markdown body.

### Proof of Concept
1. As any GitHub user, add a comment/PR body/commit message containing the bare autolink text:
   `https://github.com/<owner>/<repo>/commit/1234567/"><a href="https://evil.example.com">Legit-looking link</a>`
   Because GFM autolinking sets the anchor's `href` equal to its own inner text, `el.href === el.innerText` holds and `commitMentionUrl.test(el.href)` matches the `commit/<sha>` prefix.
2. Open this content in GitHub Desktop anywhere it's rendered via `SandboxedMarkdown` (PR quick view, notifications, commit/PR descriptions).
3. `CommitMentionLinkFilter.filter` computes `filepathToAppend = '/"><a href="https://evil.example.com">Legit-looking link</a>'` (unblocked by `isReservedCommitActionPath`) and assigns it via `newNode.innerHTML = ...` at `app/src/lib/markdown-filters/commit-mention-link-filter.ts:159-164`, producing a genuine extra `<a>` element in the sanitized document without any further sanitization pass.
4. Clicking the injected anchor triggers `setupLinkInterceptor`'s `closest('a')` handler in `app/src/ui/lib/sandboxed-markdown.tsx:292-305`, which forwards `https://evil.example.com` to `onMarkdownLinkClicked`, opening it in the user's browser instead of the GitHub commit the user believed they were viewing.

### Citations

**File:** app/src/ui/lib/sandboxed-markdown.tsx (L130-140)
```typescript
    const body = DOMPurify.sanitize(
      marked(markdown, {
        // https://marked.js.org/using_advanced  If true, use approved GitHub
        // Flavored Markdown (GFM) specification.
        gfm: true,
        // https://marked.js.org/using_advanced, If true, add <br> on a single
        // line break (copies GitHub behavior on comments, but not on rendered
        // markdown files). Requires gfm be true.
        breaks: true,
      })
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

**File:** app/src/ui/lib/sandboxed-markdown.tsx (L392-399)
```typescript
        <iframe
          title="sandboxed-markdown-component"
          className="sandboxed-markdown-component"
          sandbox="allow-same-origin"
          ref={this.onFrameRef}
          onLoad={this.refreshHeight}
          aria-label={this.props.ariaLabel}
        />
```

**File:** app/src/lib/markdown-filters/commit-mention-link-filter.ts (L140-165)
```typescript
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

**File:** app/src/lib/markdown-filters/commit-mention-link-filter.ts (L251-267)
```typescript
  private isReservedCommitActionPath(filePath: string | undefined) {
    const commitActions = [
      'checks_state_summary',
      'hovercard',
      'rollup',
      'show_partial',
    ]
    if (filePath === undefined) {
      return false
    }

    const commitActionsWithParams = ['_render_node', 'checks']
    return (
      commitActions.includes(filePath) ||
      commitActionsWithParams.includes(filePath.split('/')[0])
    )
  }
```

**File:** app/src/lib/markdown-filters/commit-mention-link-filter.ts (L272-284)
```typescript
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
