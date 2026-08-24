### Title
Unicode bidirectional control characters in commit messages/branch names are not stripped, enabling visual spoofing of Desktop's commit history, diff, and history UI - (File: `app/src/lib/text-token-parser.ts`, `app/src/lib/sanitize-ref-name.ts`)

### Summary
GitHub Desktop's `RichText`/`Tokenizer` pipeline that renders commit summaries, descriptions, branch/tag names, and other attacker-influenced strings (coming from a cloned/fetched repository or the GitHub API) does not strip Unicode bidirectional control characters (RLO/LRO/RLI/LRI/PDF, U+202A–U+202E, U+2066–U+2069) or other non-printable Unicode formatting characters before rendering them as plain text nodes.

### Finding Description
Commit summaries are taken almost verbatim from `git log` output and displayed via `RichText` in `app/src/ui/history/expandable-commit-summary.tsx` (`renderDescription`/`renderSummaryText`), which delegates tokenization to `Tokenizer` in `app/src/lib/text-token-parser.ts`. The tokenizer only special-cases `:` (emoji), `#` (issue), `@` (mention), and `h` (hyperlink) characters; every other character — including bidi control characters — is passed through untouched via `append(element)` and eventually rendered as `<span>{token.text}</span>` [1](#0-0) [2](#0-1) .

Similarly, ref/branch names are only sanitized against Git's `check-ref-format` restrictions (control chars 0x00–0x20, `~^:?*[\|"<>`, etc.) when the user creates a new local branch via `sanitizedRefName`, but this does not apply to remote branch/tag names fetched from a hostile remote and rendered as-is throughout the app (branch list, history, PR lists) [3](#0-2) .

The `SandboxedMarkdown` component (used for release notes/PR bodies) does run content through `marked` + `DOMPurify` [4](#0-3) , but neither `marked` nor `DOMPurify` strip bidi override characters, since they are valid Unicode text, not HTML/markdown syntax — this is the same class of gap called out in the external report ("encode/validate control-character injection", "the client chose to allow markdown ... which is not ideal").

The `copilot-store.ts` sanitizer (`sanitizeRuleDescription`) only strips ASCII control characters (`\u0000-\u001F`, `\u007F`) used to keep AI prompt-injection delimiters intact [5](#0-4)  — this is unrelated to display-layer bidi spoofing and does not cover the rendering paths above.

### Impact Explanation
An attacker who controls a repository the victim clones/fetches (or a PR/commit surfaced via the GitHub API) can craft a commit message, commit summary, or branch/tag name containing RLO/PDF sequences to visually reorder characters shown in Desktop's commit list, commit summary panel, diff view, and branch selectors. This is the same primitive behind the well-known "Trojan Source" / bidi-spoofing class of attacks: a benign-looking commit summary or branch name can visually disguise a malicious file name, extension, or command that the user is asked to review/confirm before committing or merging, leading the user to trust and push/merge something they did not actually read correctly. This does not achieve code execution directly, but it corrupts the integrity of what the user believes they are reviewing and can facilitate social-engineering-free trust decisions (e.g., approving a merge, trusting a commit's stated intent) — squarely inside the "silent corruption of what the user commits/pushes" impact class the report accepts.

### Likelihood Explanation
High feasibility: any public/malicious remote can push a commit with a crafted UTF-8 summary or a branch name containing bidi control characters (branch names are far more restrictive at the Git protocol level for local ref creation, but Desktop does not re-validate ref names fetched from a remote before display). No special git configuration or local access is required — simply cloning or fetching a hostile repository, or having a hostile PR appear in Desktop's PR list, is sufficient to trigger rendering.

### Recommendation
Strip or visually neutralize Unicode bidirectional control characters (and other invisible formatting characters, e.g. zero-width space/joiner) before passing commit summaries, descriptions, branch names, and other API-derived strings to `RichText`/`Tokenizer`, similar to how `sanitizeRuleDescription` already strips ASCII control characters for the Copilot prompt path. Consider isolating each `TokenType.Text` token with explicit Unicode isolation marks, or replacing bidi control characters with a visible placeholder, and apply the same filter inside `Tokenizer.append`/`flush` so both commit messages and PR/issue metadata are covered.

### Proof of Concept
1. In a repository you control, create a commit whose summary contains an RLO character followed by reversed text designed to visually spoof a different intent, e.g.:
   `git commit -m "Fix bug \u202Etxt.exe\u202C for logging"`
2. Push this commit and have the victim clone/fetch the repository in GitHub Desktop.
3. Open the commit in Desktop's History view; `expandable-commit-summary.tsx` renders the summary via `RichText`, which passes the raw string (including the RLO/PDF characters) straight through `Tokenizer.tokenize` into a `<span>` [6](#0-5) , causing the trailing text to render visually reversed/spoofed in the commit list and summary panel, misrepresenting the actual committed content to the reviewing user.

### Citations

**File:** app/src/lib/text-token-parser.ts (L272-298)
```typescript
  private tokenizeNonGitHubRepository(
    text: string
  ): ReadonlyArray<TokenResult> {
    let i = 0
    while (i < text.length) {
      const element = text[i]
      switch (element) {
        case ':':
          i = this.inspectAndMove(element, i, () => this.scanForEmoji(text, i))
          break

        case 'h':
          i = this.inspectAndMove(element, i, () =>
            this.scanForHyperlink(text, i)
          )
          break

        default:
          this.append(element)
          i++
          break
      }
    }

    this.flush()
    return this._results
  }
```

**File:** app/src/ui/lib/rich-text.tsx (L73-78)
```typescript
      case TokenType.Text:
        return <span key={index}>{token.text}</span>
      default:
        return assertNever(token, `Unknown token type: ${token}`)
    }
  })
```

**File:** app/src/lib/sanitize-ref-name.ts (L1-16)
```typescript
// See https://www.kernel.org/pub/software/scm/git/docs/git-check-ref-format.html
// ASCII Control chars and space, DEL, ~ ^ : ? * [ \
// | " < and > is technically a valid refname but not on Windows
// the magic sequence @{, consecutive dots, leading and trailing dot, ref ending in .lock
const invalidCharacterRegex =
  /[\x00-\x20\x7F~^:?*\[\\|""<>]+|@{|\.\.+|^\.|\.$|\.lock$|\/$/g

/** Sanitize a proposed reference name by replacing illegal characters. */
export function sanitizedRefName(name: string): string {
  return name.replace(invalidCharacterRegex, '-').replace(/^[-\+]*/g, '')
}

/** Validate that a reference does not contain any invalid characters */
export function testForInvalidChars(name: string): boolean {
  return invalidCharacterRegex.test(name)
}
```

**File:** app/src/ui/lib/sandboxed-markdown.tsx (L127-140)
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

**File:** app/src/lib/stores/copilot-store.ts (L291-297)
```typescript
 * Strips control characters (including newlines) and surrounding whitespace
 * from a single rule description so it renders as a single bullet line and
 * can't fragment the surrounding delimited block.
 */
function sanitizeRuleDescription(description: string): string {
  return description.replace(/[\u0000-\u001F\u007F]+/g, ' ').trim()
}
```
