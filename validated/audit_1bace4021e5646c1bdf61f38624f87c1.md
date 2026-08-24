Based on the investigation, the strongest local analog to the "arbitrary attacker-supplied string used unsanitized in a security-sensitive path" bug class from the Solidity report is in GitHub Desktop's custom-protocol ("Open in Desktop") URL parser, not in the markdown/link-button rendering paths (those are protected — see `sandboxed-markdown.tsx`'s `setupLinkInterceptor` which only forwards `https?:` links, and `enterprise-validate-url.ts` which enforces `https:` for GHE server addresses). [1](#0-0) 

### Title
Unsanitized `url` field from custom protocol handler (`x-github-client://openRepo/...`) reaches repository clone flow without validation - (File: app/src/lib/parse-app-url.ts)

### Summary
GitHub Desktop registers several OS-level custom URL-scheme protocol handlers (`x-github-client`, `x-github-desktop-auth`/`x-github-desktop-dev-auth`, and legacy `github-mac`/`github-windows`) that any webpage, email, or document can invoke via a simple `<a href="...">` link, with no user confirmation gate visible before `handleAppURL` is invoked. [2](#0-1) [3](#0-2) 

When the action name is `openrepo`, `parseAppURL` validates the `branch` parameter with `testForInvalidChars` and the `pr` parameter with a strict digits-only regex, but the `url` field itself — the value that identifies the repository to clone/open — is taken verbatim from the path component with **no validation at all** beyond simply being non-empty. [4](#0-3) 

Test cases confirm `url` can be arbitrary text, including SSH-style strings like `git@github.com/desktop/desktop`, with no scheme/format checks applied. [5](#0-4) 

### Finding Description
This mirrors the report's broken invariant exactly: a field that downstream code treats as a trusted identifier/URL (`token_uri` in the Solidity report, `url` here) is actually fully attacker-controlled and unsanitized. The asymmetry is telling — the authors clearly recognized `branch` needed sanitization against invalid/injectable characters (`testForInvalidChars`), but applied no equivalent guard to `url`, even though `url` is the value ultimately used to identify/clone a git remote. Because the OS-level protocol handlers are globally registered, the attacker does not need local access, admin rights, or any pre-existing compromise — they only need the victim to click a link (e.g., embedded in a malicious webpage, or a GitHub issue/README rendered as HTML) pointing to `x-github-client://openRepo/<attacker-string>`.

### Impact Explanation
If the unsanitized `url` is subsequently passed as a positional argument to git plumbing/porcelain commands (e.g., `git clone <url>`) without a `--` separator or a check that it doesn't begin with `-`, this is the classic git argument-injection primitive: a string like `--upload-pack=touch /tmp/pwned;` or an `ext::` transport URL can cause git to execute an arbitrary local command during the "clone," achieving code execution outside the intended repo boundary — directly matching the in-scope impact categories (code execution / file write outside repo via a "git remote/proxy response"-adjacent vector, here a crafted deep-link acting as the remote source).

### Likelihood Explanation
Medium-to-High for reachability of the *unsanitized field*, but I was **not able to fully confirm, within my remaining tool budget, the exact downstream consumer of `IOpenRepositoryFromURLAction.url`** (the `case 'open-repository-from-url'` handling in `app/src/ui/dispatcher/dispatcher.ts`, and the specific git invocation it feeds into — e.g., whether it uses `execFile` with an argument array and a `--` guard, which would mitigate the argument-injection primitive even though the string itself remains unsanitized at the parser level). This is a genuine gap in my verification: the finding is solid at the parsing layer (verified absence of validation), but the full exploit chain (parser → dispatcher → clone-repository logic → git invocation) needs confirmation to establish definitive RCE versus a lesser info/DoS outcome.

### Recommendation
Apply the same character/format validation to `url` in `parseAppURL` that is already applied to `branch` (reject values starting with `-`, containing `://` schemes other than `https:`/`ssh:`, or otherwise resembling command-line flags), and ensure any git invocation consuming this value uses a `--` argument separator before untrusted positional arguments.

### Proof of Concept
Not independently executed — this report is based on static code review only, per the constraints of this task, and the exploit chain from `parseAppURL` output through to any `git clone` call was not fully traced due to tool-call budget exhaustion. A background engineering session (with full file/terminal access) would be needed to:
1. Trace `case 'open-repository-from-url'` in `app/src/ui/dispatcher/dispatcher.ts` to the clone/open implementation.
2. Determine whether the resulting `url` string reaches a `git` command invocation as a raw/untrusted argument without a `--` separator or leading-dash rejection.
3. If confirmed, craft `x-github-client://openRepo/--upload-pack=<payload>` (or equivalent) as a concrete PoC link.

Given this unresolved gap, I present this as the strongest local analog found, but flag it as requiring further confirmation rather than a fully proven end-to-end exploit.

### Citations

**File:** app/src/lib/parse-app-url.ts (L10-24)
```typescript
export interface IOpenRepositoryFromURLAction {
  readonly name: 'open-repository-from-url'

  /** the remote repository location associated with the "Open in Desktop" action */
  readonly url: string

  /** the optional branch name which should be checked out. use the default branch otherwise. */
  readonly branch: string | null

  /** the pull request number, if pull request originates from a fork of the repository */
  readonly pr: string | null

  /** the file to open after cloning the repository */
  readonly filepath: string | null
}
```

**File:** app/src/lib/parse-app-url.ts (L66-125)
```typescript
export function parseAppURL(url: string): URLActionType {
  const parsedURL = URL.parse(url, true)
  const hostname = parsedURL.hostname
  const unknown: IUnknownAction = { name: 'unknown', url }
  if (!hostname) {
    return unknown
  }

  const query = parsedURL.query

  const actionName = hostname.toLowerCase()
  if (actionName === 'oauth') {
    const code = getQueryStringValue(query, 'code')
    const state = getQueryStringValue(query, 'state')
    if (code != null && state != null) {
      return { name: 'oauth', code, state }
    } else {
      return unknown
    }
  }

  // we require something resembling a URL first
  // - bail out if it's not defined
  // - bail out if you only have `/`
  const pathName = parsedURL.pathname
  if (!pathName || pathName.length <= 1) {
    return unknown
  }

  // Trim the trailing / from the URL
  const parsedPath = pathName.substring(1)

  if (actionName === 'openrepo') {
    const pr = getQueryStringValue(query, 'pr')
    const branch = getQueryStringValue(query, 'branch')
    const filepath = getQueryStringValue(query, 'filepath')

    if (pr != null) {
      if (!/^\d+$/.test(pr)) {
        return unknown
      }

      // we also expect the branch for a forked PR to be a given ref format
      if (branch != null && !/^pr\/\d+$/.test(branch)) {
        return unknown
      }
    }

    if (branch != null && testForInvalidChars(branch)) {
      return unknown
    }

    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
    }
  }
```

**File:** app/src/main-process/main.ts (L105-116)
```typescript
const possibleProtocols = new Set(['x-github-client'])
if (__DEV_SECRETS__) {
  possibleProtocols.add('x-github-desktop-dev-auth')
} else {
  possibleProtocols.add('x-github-desktop-auth')
}
// Also support Desktop Classic's protocols.
if (__DARWIN__) {
  possibleProtocols.add('github-mac')
} else if (__WIN32__) {
  possibleProtocols.add('github-windows')
}
```

**File:** app/src/main-process/main.ts (L204-210)
```typescript
app.on('will-finish-launching', () => {
  // macOS only
  app.on('open-url', (event, url) => {
    event.preventDefault()
    handleAppURL(url)
  })
})
```

**File:** app/test/unit/parse-app-url-test.ts (L96-105)
```typescript
  describe('openRepo via SSH', () => {
    it('returns right name', () => {
      const result = parseAppURL(
        'github-mac://openRepo/git@github.com/desktop/desktop'
      )
      assert.equal(result.name, 'open-repository-from-url')

      const openRepo = result as IOpenRepositoryFromURLAction
      assert.equal(openRepo.url, 'git@github.com/desktop/desktop')
    })
```
