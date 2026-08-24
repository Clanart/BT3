## Analysis Confirmed

Both flaws the question describes are real and combine exactly as suggested.

### The two flaws in `githubAssetVideoRegex` [1](#0-0) 

1. **Missing host boundary**: the regex is built as `'^' + escapeRegExp(user_images_cdn_url) + '.+' + videoExtensionRegex.source`. There is no `/` (or `$`/word-boundary) required immediately after `https://user-images.githubusercontent.com`. Anything, including another domain label like `.attacker.evil`, is legal there because it just falls inside the following `.+`.
2. **Vacuous extension alternation**: `videoExtensionRegex` is `/(mp4|webm|ogg|mov|qt|avi|wmv|3gp|mpg|mpeg|)$/` — note the trailing empty alternative (`mpeg|)`). This makes the "extension check" match the empty string at the end of *any* string, so it never actually rejects anything.

### Proof

For the string `https://user-images.githubusercontent.com.attacker.evil/payload`:
- `^https://user-images.githubusercontent.com` matches the literal prefix of the string (true prefix, unrelated to real host boundaries).
- `.+` greedily consumes the remaining `.attacker.evil/payload`.
- The extension group matches its empty alternative right before `$`.

So `githubAssetVideoRegex.test('https://user-images.githubusercontent.com.attacker.evil/payload')` returns `true`.

### Where this is exploited

- `VideoTagFilter` uses this exact test to decide whether to **keep** a `<video>` tag's `src` instead of stripping it: [2](#0-1)  and [3](#0-2) 
- `VideoLinkFilter` uses the same test to decide whether to **auto-convert** an `<a href="...">` link into an autoloading `<video>` element: [4](#0-3)  and [5](#0-4) 

Since both filters gate solely on `githubAssetVideoRegex.test(url)`, a single crafted URL like `https://user-images.githubusercontent.com.attacker.evil/payload` passes both checks: `VideoTagFilter` will leave the `<video src="...">` in place instead of stripping it, and `VideoLinkFilter` will actively rewrite a plain link into a `<video>` tag pointing at the attacker's domain, causing Desktop's renderer to auto-fetch content from `attacker.evil` when rendering GitHub-flavored markdown (issue/PR bodies, commit messages, etc.) that an attacker fully controls.

### Title
CDN allowlist bypass in `githubAssetVideoRegex` due to missing host-boundary and vacuous extension check — arbitrary-domain video auto-embedding in rendered markdown - (File: `app/src/lib/markdown-filters/video-url-regex.ts`)

### Summary
The regex meant to restrict embeddable/auto-linked video sources to GitHub's `user-images.githubusercontent.com` CDN can be satisfied by any URL whose host merely *starts with* that string as a text prefix (e.g. `user-images.githubusercontent.com.attacker.evil`), and the "extension" portion of the check is a no-op due to an empty alternation branch. This single defect defeats both consumers of the regex, `VideoTagFilter` and `VideoLinkFilter`.

### Finding Description
`githubAssetVideoRegex` is constructed via string concatenation of an escaped prefix, an unconstrained `.+`, and an extension-matching group ending in `$`, with no assertion that the character following the CDN host is a `/` or that the matched host is exactly `user-images.githubusercontent.com`. Combined with the extension group's empty alternative, the whole regex degenerates to effectively "starts with the literal text `https://user-images.githubusercontent.com`" — a check trivially bypassed by attacker-controlled DNS names that share that prefix (e.g. `user-images.githubusercontent.com.attacker.evil`), which is a domain a third party can freely register and control. [6](#0-5) 

### Impact Explanation
Rendered GitHub-flavored markdown (issue bodies, PR descriptions, commit messages, comments) that an attacker controls can contain either a raw `<video src="https://user-images.githubusercontent.com.attacker.evil/x">` tag or a plain link that GitHub Desktop's `VideoLinkFilter` will auto-convert into a `<video>` element. Because both filters rely solely on this broken regex, the attacker-controlled `<video>` element is rendered and its `src` is fetched automatically by Desktop's renderer, without user interaction, from a domain the attacker fully controls — defeating the intended CDN allowlist and enabling automatic outbound requests (IP/user-agent leakage, tracking, and exposure to whatever content-type confusion or renderer bugs the fetched response can trigger) to an attacker-chosen origin.

### Likelihood Explanation
Any attacker who can get content rendered through Desktop's markdown pipeline (e.g., a public repo issue/PR/commit message viewed in Desktop) can trivially craft this payload; no user action beyond viewing the rendered content is required, and registering a domain like `attacker.evil` with a subdomain label containing the CDN prefix string is straightforward.

### Recommendation
Parse the URL properly (e.g., via the `URL` constructor) and validate that `url.protocol === 'https:'` and `url.hostname === 'user-images.githubusercontent.com'` exactly, rather than doing prefix string matching. Additionally fix `videoExtensionRegex` to not include the empty-string alternative, so the extension check is meaningful (or drop it if extension enforcement isn't actually required for the allowlist).

### Proof of Concept
```js
const { githubAssetVideoRegex } = require('./video-url-regex')
console.log(githubAssetVideoRegex.test(
  'https://user-images.githubusercontent.com.attacker.evil/payload'
)) // -> true, should be false
```
Embedding `<video src="https://user-images.githubusercontent.com.attacker.evil/payload"></video>` in rendered markdown will not be stripped by `VideoTagFilter`, and a plain link to that same URL inside a `<p><a>` will be auto-converted into a `<video>` tag by `VideoLinkFilter`.

### Citations

**File:** app/src/lib/markdown-filters/video-url-regex.ts (L3-14)
```typescript
const user_images_cdn_url = 'https://user-images.githubusercontent.com'

// List of common video formats obtained from
// https://developer.mozilla.org/en-US/docs/https://developer.mozilla.org/en-US/docs/Web/Media/Formats/Video_codecs/Media/Formats/Video_codecs
// The MP4, WebM, and Ogg formats are supported by HTML standard.
const videoExtensionRegex = /(mp4|webm|ogg|mov|qt|avi|wmv|3gp|mpg|mpeg|)$/

/** Regex for checking if a url is a github asset cdn video url */
export const githubAssetVideoRegex = new RegExp(
  '^' + escapeRegExp(user_images_cdn_url) + '.+' + videoExtensionRegex.source,
  'i'
)
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

**File:** app/src/lib/markdown-filters/video-tag-filter.ts (L30-40)
```typescript
  public async filter(node: Node): Promise<ReadonlyArray<Node> | null> {
    if (!isElement(node, 'video') || githubAssetVideoRegex.test(node.src)) {
      // If it is video element with a valid source, we return null to leave it alone.
      // This is different than dotcom which regenerates a video tag because it
      // verifies through a db call that the assets exists
      return null
    }

    // Return empty array so that video tag is removed
    return []
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
