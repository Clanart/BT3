[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** app/src/lib/markdown-filters/video-url-regex.ts (L1-14)
```typescript
import escapeRegExp from 'lodash/escapeRegExp'

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

**File:** app/src/lib/markdown-filters/video-tag-filter.ts (L27-40)
```typescript
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

**File:** app/src/ui/lib/sandboxed-markdown.tsx (L164-174)
```typescript
    // We used this `Buffer.toString('base64')` approach because `btoa` could not
    // convert non-latin strings that existed in the markedjs.
    const b64src = Buffer.from(src, 'utf8').toString('base64')

    // We are using `src` and data uri as opposed to an html string in the
    // `srcdoc` property because the `srcdoc` property renders the html in the
    // parent dom and we want all rendering to be isolated to our sandboxed iframe.
    // -- https://csplite.com/csp/test188/
    const oldDocument = this.frameRef.contentDocument
    this.currentDocument = null
    this.frameRef.src = `data:text/html;charset=utf-8;base64,${b64src}`
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
