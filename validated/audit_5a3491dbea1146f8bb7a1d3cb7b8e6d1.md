No vulnerability found for this question.

**Explanation:** `app/src/lib/emoji.ts` contains only a TypeScript type declaration (`Emoji`) with no parsing, rendering, or DOM logic whatsoever. [1](#0-0) 

The actual emoji substitution logic that consumes this type lives elsewhere, and none of it inserts attacker-controlled text as markup:

1. **`EmojiFilter`** (used for markdown rendering) walks text nodes and only ever creates emoji nodes via `document.createTextNode`, `span.textContent =`, or an `<img>` whose `src` is a base64 data URI built from a **local file path** looked up from the internal emoji map — never from attacker text — and whose `alt` is set via the DOM property (not `innerHTML`). [2](#0-1) 

2. **`Tokenizer`** (used for commit messages/titles via `text-token-parser.ts`) only recognizes emoji refs that exist as keys in the trusted `allEmoji` map; unmatched `:foo:` sequences are emitted as plain `TokenType.Text`. [3](#0-2) 

3. **`rich-text.tsx`** renders tokens through JSX, which auto-escapes text content (`<span>{token.text}</span>`), and only sets `img src` to `token.path` (the internal emoji URL, not attacker text). [4](#0-3) 

4. Hyperlink tokens are rendered via `LinkButton`, which sets `href` as a React JSX attribute (auto-escaped, not `dangerouslySetInnerHTML`), and clicking calls `shell.openExternal(uri)` — no `javascript:`/markup execution path exists here. [5](#0-4) 

None of this pipeline uses `innerHTML`, `dangerouslySetInnerHTML`, or `eval`-like sinks for untrusted text; text is always inserted as DOM text nodes or JSX text children, both of which are escaped/non-executable. There is no code path by which "malformed Actions-log/ANSI/diff bytes" parsed as emoji references could produce injected markup, a `javascript:`/`file:` URI, or reach `shell.openExternal`, IPC, or Node APIs beyond what already-reviewed, scheme-agnostic `openExternal` behavior provides (a pre-existing, separate design decision, not something `Emoji`/`emoji.ts` introduces). The premise that `Emoji`/`emoji.ts` itself performs parsing or rendering is incorrect — the file has no executable logic at all.

### Citations

**File:** app/src/lib/emoji.ts (L1-18)
```typescript
/** Represents an emoji */
export type Emoji = {
  /**
   * The unicode string of the emoji if emoji is part of
   * the unicode specification. If missing this emoji is
   * a GitHub custom emoji such as :shipit:
   */
  readonly emoji?: string

  /** URL of the image of the emoji (alternative to the unicode character) */
  readonly url: string

  /** One or more human readable aliases for the emoji character */
  readonly aliases: ReadonlyArray<string>

  /** An optional, human readable, description of the emoji  */
  readonly description?: string
}
```

**File:** app/src/lib/markdown-filters/emoji-filter.ts (L104-140)
```typescript
  private async createEmojiNode(
    emoji: Emoji
  ): Promise<HTMLImageElement | HTMLSpanElement | null> {
    try {
      if (emoji.emoji) {
        const emojiSpan = document.createElement('span')
        emojiSpan.classList.add('emoji')
        emojiSpan.textContent = emoji.emoji
        return emojiSpan
      }

      const dataURI = await this.getBase64FromImageUrl(emoji.url)
      const emojiImg = new Image()
      emojiImg.classList.add('emoji')
      emojiImg.src = dataURI
      emojiImg.alt = emoji.description ?? ''
      return emojiImg
    } catch (e) {}
    return null
  }

  /**
   * Method to obtain an images base 64 data uri from it's file path.
   * - It checks cache, if not, reads from file, then stores in cache.
   */
  private async getBase64FromImageUrl(filePath: string): Promise<string> {
    const cached = this.emojiBase64URICache.get(filePath)
    if (cached !== undefined) {
      return cached
    }
    const imageBuffer = await readFile(fileURLToPath(filePath))
    const b64src = imageBuffer.toString('base64')
    const uri = `data:image/png;base64,${b64src}`
    this.emojiBase64URICache.set(filePath, uri)

    return uri
  }
```

**File:** app/src/lib/text-token-parser.ts (L116-137)
```typescript
  private scanForEmoji(text: string, index: number): LookupResult | null {
    const nextIndex = this.scanForEndOfWord(text, index)
    const maybeEmoji = text.slice(index, nextIndex)
    if (!/^:.*?:$/.test(maybeEmoji)) {
      return null
    }

    const emoji = this.allEmoji.get(maybeEmoji)
    if (!emoji) {
      return null
    }

    this.flush()
    this._results.push({
      kind: TokenType.Emoji,
      text: maybeEmoji,
      path: emoji.url,
      emoji: emoji.emoji,
      description: emoji.description,
    })
    return { nextIndex }
  }
```

**File:** app/src/ui/lib/rich-text.tsx (L49-61)
```typescript
      case TokenType.Emoji:
        if (token.emoji) {
          return <span key={index}>{token.emoji}</span>
        } else {
          return (
            <img
              key={index}
              alt={token.description ?? token.text}
              className="emoji"
              src={token.path}
            />
          )
        }
```

**File:** app/src/ui/lib/link-button.tsx (L56-92)
```typescript
    return (
      <a
        ref={this.anchorRef}
        className={className}
        href={href}
        onMouseOver={this.props.onMouseOver}
        onMouseOut={this.props.onMouseOut}
        onFocus={this.props.onMouseOver}
        onBlur={this.props.onMouseOut}
        onClick={this.onClick}
        tabIndex={this.props.tabIndex}
        aria-label={this.props.ariaLabel}
        role={role}
      >
        {title && <Tooltip target={this.anchorRef}>{title}</Tooltip>}
        {this.props.children}
      </a>
    )
  }

  private onClick = (event: React.MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault()

    if (this.props.disabled) {
      return
    }

    const uri = this.props.uri
    if (uri) {
      shell.openExternal(uri)
    }

    const onClick = this.props.onClick
    if (onClick) {
      onClick()
    }
  }
```
