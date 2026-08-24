## Finding: `githubAssetVideoRegex` uses an unanchored prefix check, allowing host-confusion bypass

### Summary
The regex used by both `VideoTagFilter` and `VideoLinkFilter` to gate which video URLs may be rendered as a `<video src="...">` element only checks that a string **starts with** the literal `https://user-images.githubusercontent.com`, without requiring a path separator, dot, or end-of-string boundary immediately after the host. Combined with a broken "extension" check that always matches (because its alternation includes an empty option), any attacker-controlled URL whose string begins with that exact prefix will pass validation, even if the actual host is entirely different (e.g. `user-images.githubusercontent.com.evil.example`).

### Finding Description
`githubAssetVideoRegex` is built as: [1](#0-0) 

The pattern is `^https://user-images\.githubusercontent\.com.+(mp4|webm|ogg|mov|qt|avi|wmv|3gp|mpg|mpeg|)$` (escaped) with the `i` flag. Two problems compound:

1. **No boundary after the host.** There is no `/` or `.` required right after the literal host string, so `.+` can absorb attacker text directly appended to the trusted prefix, e.g. `https://user-images.githubusercontent.com.evil.example/x.mp4`, which still starts with the exact literal `https://user-images.githubusercontent.com`. This is a textbook "starts-with" allowlist bypass — DNS labels are read right-to-left, so `user-images.githubusercontent.com.evil.example` is a subdomain the attacker fully controls via `evil.example`.
2. **The extension check is a no-op.** `videoExtensionRegex` is `(mp4|webm|ogg|mov|qt|avi|wmv|3gp|mpg|mpeg|)$` — note the trailing empty alternative before `$`. Because the alternation can match an empty string, the regex engine can always backtrack `.+` to consume the remainder of the string and satisfy the empty alternative before `$`. This means the "must end in a known video extension" restriction never actually filters anything, so the attacker's crafted suffix can be arbitrary.

This regex is the sole gate used in both consumers: [2](#0-1) [3](#0-2) 

`VideoLinkFilter` even actively constructs and injects a new `<video>` element with `src` set from the attacker-controlled `href` once the (broken) regex passes: [4](#0-3) 

These filters run inside `SandboxedMarkdown.applyFilters`, which executes *after* `DOMPurify.sanitize` has already produced the DOM inside the iframe, so DOMPurify's own attribute sanitization does not re-validate the `src` value that these filters inject: [5](#0-4) [6](#0-5) 

### Impact Explanation
An attacker who controls PR/issue/commit markdown body content (rendered via `SandboxedMarkdown` in PR comments, PR quick view, release notes, etc.) can cause GitHub Desktop to emit a `<video src="...">` pointing at an attacker-controlled origin instead of a real `user-images.githubusercontent.com` asset, corrupting the intended "only recognized safe video hosts get embedded" invariant. Because the iframe is loaded from a `data:` URI, its origin is an opaque/unique origin, not `https://github.com`, so no GitHub session cookies would be attached to the resulting request — this rules out direct credential/cookie exfiltration via this path. The realistic impact is an **unprompted, uninteracted-with outbound network request from the renderer to an attacker-chosen host** (leaking the victim's IP address/User-Agent and confirming markdown was viewed), and a break of the origin-restriction invariant the filter is explicitly documented to enforce.

### Likelihood Explanation
High likelihood of triggering: any attacker who can post a PR/issue comment or PR body (unprivileged, standard GitHub interaction) can craft a URL such as `https://user-images.githubusercontent.com.evil.example/x.mp4`. Loading requires only that the victim view the PR/comment/commit markdown in Desktop — no click needed, since `<video>` elements can eagerly fetch a poster/first bytes depending on `preload` attribute defaults.

### Recommendation
Fix `githubAssetVideoRegex` in `app/src/lib/markdown-filters/video-url-regex.ts` to properly parse the URL (e.g. via the `URL` constructor) and strictly compare `url.origin`/`url.hostname` against `'user-images.githubusercontent.com'` rather than doing a string-prefix regex match. Also fix `videoExtensionRegex` so the extension alternation cannot match an empty string (drop the trailing `|` in the alternation, or require a `.` before the extension).

### Proof of Concept
1. Post a PR comment (or issue body rendered through `SandboxedMarkdown`) containing an auto-linked URL:
   `https://user-images.githubusercontent.com.evil.example/x.mp4`
2. `marked`/DOMPurify converts this to `<p><a href="https://user-images.githubusercontent.com.evil.example/x.mp4">...</a></p>`.
3. `VideoLinkFilter.getGithubVideoLink` tests `githubAssetVideoRegex.test(href)` — this returns `true` despite the host actually being `user-images.githubusercontent.com.evil.example` (attacker's DNS zone under `evil.example`), because the regex only checks the literal prefix and the extension alternative is a no-op.
4. The filter emits `<video src="https://user-images.githubusercontent.com.evil.example/x.mp4"></video>`, which `VideoTagFilter` then also confirms as "valid" and leaves in place.
5. When rendered, the sandboxed iframe issues a network request to the attacker's server for the video resource — the "only github user-images host" invariant has been broken.

### Citations

**File:** app/src/lib/markdown-filters/video-url-regex.ts (L8-13)
```typescript
const videoExtensionRegex = /(mp4|webm|ogg|mov|qt|avi|wmv|3gp|mpg|mpeg|)$/

/** Regex for checking if a url is a github asset cdn video url */
export const githubAssetVideoRegex = new RegExp(
  '^' + escapeRegExp(user_images_cdn_url) + '.+' + videoExtensionRegex.source,
  'i'
```

**File:** app/src/lib/markdown-filters/video-tag-filter.ts (L17-25)
```typescript
  public createFilterTreeWalker(doc: Document): TreeWalker {
    return doc.createTreeWalker(doc.body, NodeFilter.SHOW_ELEMENT, {
      acceptNode: function (el: Element) {
        return !isElement(el, 'video') || githubAssetVideoRegex.test(el.src)
          ? NodeFilter.FILTER_SKIP
          : NodeFilter.FILTER_ACCEPT
      },
    })
  }
```

**File:** app/src/lib/markdown-filters/video-link-filter.ts (L46-55)
```typescript
  public async filter(node: Node): Promise<ReadonlyArray<Node> | null> {
    const videoSrc = this.getGithubVideoLink(node)
    if (videoSrc === null) {
      return null
    }

    const videoNode = document.createElement('video')
    videoNode.src = videoSrc
    return [videoNode]
  }
```

**File:** app/src/lib/markdown-filters/video-link-filter.ts (L68-80)
```typescript
  private getGithubVideoLink(node: Node): string | null {
    if (
      isElement(node, 'p') &&
      node.childElementCount === 1 &&
      node.firstChild &&
      isElement(node.firstChild, 'a') &&
      githubAssetVideoRegex.test(node.firstChild.href)
    ) {
      return node.firstChild.href
    }

    return null
  }
```

**File:** app/src/ui/lib/sandboxed-markdown.tsx (L127-141)
```typescript
  public renderMarkdown = async () => {
    const { markdown } = this.props

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

**File:** app/src/ui/lib/sandboxed-markdown.tsx (L320-339)
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
```
