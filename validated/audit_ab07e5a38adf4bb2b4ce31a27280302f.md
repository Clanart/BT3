## Title
Loose CDN-prefix regex in `githubAssetVideoRegex` allows lookalike-domain video URLs to bypass GitHub asset host validation - (File: app/src/lib/markdown-filters/video-url-regex.ts)

### Summary
`githubAssetVideoRegex` is used as the sole gate that decides whether an untrusted `<video>` `src` (from a rendered commit/PR markdown body) is treated as a trusted `user-images.githubusercontent.com` asset. The regex is built as a plain string prefix match with no origin/host boundary, so it can be satisfied by attacker-controlled domains that merely start with the expected string.

### Finding Description
The regex is constructed as:
```
'^' + escapeRegExp('https://user-images.githubusercontent.com') + '.+' + videoExtensionRegex.source
``` [1](#0-0) 

This only anchors the **start** of the string to the literal `https://user-images.githubusercontent.com` — there is no required delimiter (e.g. `/` or `\b`) immediately after the host, and no end-of-string anchor before the extension check. As a result, a value such as `https://user-images.githubusercontent.com.attacker.example/evil.mp4` satisfies `^https:\/\/user\-images\.githubusercontent\.com` (as a literal prefix match on the whole hostname string), then `.+` consumes `.attacker.example/evil`, and the trailing `videoExtensionRegex` matches `mp4`. The regex therefore returns `true` for a domain that is not actually `user-images.githubusercontent.com`, but merely has it as a hostname *prefix substring*.

This regex gates two untrusted-input sinks:
- `VideoTagFilter.filter`/`createFilterTreeWalker`, which decides whether to keep or strip a `<video src="...">` element parsed from rendered markdown (PR/commit/issue body) [2](#0-1) 
- `VideoLinkFilter.getGithubVideoLink`, which rewrites an auto-linked `<a href="...">` into a `<video src="...">` element if the href passes the same regex [3](#0-2) 

Both `src`/`href` values originate from repository/API content rendered as markdown, i.e., attacker-controlled input in scope.

### Impact Explanation
Because the regex fails to enforce a real origin boundary, an attacker who controls PR/commit/issue markdown content can craft a video/link URL like `https://user-images.githubusercontent.com.attacker.example/evil.mp4` (or a URL containing `user-images.githubusercontent.com` followed directly by more path/host characters without a `/`) that is misclassified as a legitimate GitHub asset. This lets an attacker-hosted resource be rendered as a `<video>` element inside the Desktop UI where the user reasonably trusts it to be GitHub-hosted content, and it defeats the intended allow-listing purpose of this check (the code comment explicitly states the goal is to only allow through URLs matching "a github user asset"). No CSP `media-src` restriction was found in the repository to provide a defense-in-depth backstop for this specific check.

However, within this codebase's current design, `<video>` elements only render whatever media the `src` points to (no script execution context) — I could not find any escalation path in this repo (e.g. no evidence of arbitrary local file read via `file://`, no observed IPC bridge that video rendering can reach) that turns this into code execution, credential exfiltration, or file read/write. The demonstrated impact is limited to bypassing the host allow-list to load/display attacker-hosted video content that impersonates a GitHub CDN asset.

### Likelihood Explanation
High likelihood of triggering: an attacker only needs to include such a URL in markdown of a PR/commit/issue body, or as a link that gets auto-linked and passed through `VideoLinkFilter`. No user interaction beyond normal viewing of the rendered content is required.

### Recommendation
Anchor the check to an actual origin/hostname comparison instead of a raw string-prefix regex. For example, parse the URL and compare `new URL(src).host === 'user-images.githubusercontent.com'` (or require a `/` or end-of-string immediately after the escaped host in the regex, e.g. `'^' + escapeRegExp(user_images_cdn_url) + '(?:/|$)' + ...`), so that domains such as `user-images.githubusercontent.com.attacker.example` are correctly rejected.

### Proof of Concept
Using `VideoTagFilter`/`githubAssetVideoRegex` directly:
```ts
import { githubAssetVideoRegex } from '../../src/lib/markdown-filters/video-url-regex'

const lookalike = 'https://user-images.githubusercontent.com.attacker.example/evil.mp4'
console.log(githubAssetVideoRegex.test(lookalike)) // true — bypasses intended host allow-list
```
Feeding a `<video src="https://user-images.githubusercontent.com.attacker.example/evil.mp4">` (or the equivalent auto-linked anchor) inside a PR/commit body will pass `VideoTagFilter.createFilterTreeWalker`'s `acceptNode` check [4](#0-3)  and be left in the rendered DOM (or, via `VideoLinkFilter`, be converted into a `<video>` element) [5](#0-4) , causing GitHub Desktop to load and display attacker-hosted media as if it were a trusted GitHub asset.

### Citations

**File:** app/src/lib/markdown-filters/video-url-regex.ts (L11-14)
```typescript
export const githubAssetVideoRegex = new RegExp(
  '^' + escapeRegExp(user_images_cdn_url) + '.+' + videoExtensionRegex.source,
  'i'
)
```

**File:** app/src/lib/markdown-filters/video-tag-filter.ts (L17-40)
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

  /**
   * Takes a video element who's src host is not a github user asset url and removes it.
   */
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
