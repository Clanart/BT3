Based on the investigation, the strongest analog to the vesting contract's "missing bound/validation check leading to attacker-controlled value being used unclamped" pattern is in GitHub Desktop's deep-link URL parser, where a query parameter is accepted from an attacker-controlled link without the same validation applied to sibling parameters.

### Title
Unvalidated `filepath` Query Parameter in "Open in Desktop" Deep Link Allows Path Traversal - (File: app/src/lib/parse-app-url.ts)

### Summary
`parseAppURL` validates the `branch` and `pr` query-string values of an `openRepo` deep link with `testForInvalidChars`/regex checks before using them, but the `filepath` parameter — described as "the file to open after cloning the repository" — is read via `getQueryStringValue` and passed through completely unvalidated. [1](#0-0) [2](#0-1) 

### Finding Description
The vesting bug's root cause was that one input (accrued/initial-unlock amount) was bounds-checked while a related accumulation path was not, letting the unchecked path exceed the invariant (`totalAmount`). The Desktop analog is structurally identical: for the same `open-repository-from-url` action, `branch` is checked with `testForInvalidChars` (which blocks `..`, `/`, control characters, etc.) and `pr` is checked with a strict `^\d+$` regex, but `filepath` — which flows into `IOpenRepositoryFromURLAction.filepath` and is documented as controlling which file gets opened after clone — receives no equivalent check: [2](#0-1) [1](#0-0) 

The existing test suite confirms `filepath` is accepted as-is, including with `/` characters preserved verbatim (e.g. `Octokit.Reactive/Octokit.Reactive.csproj`), with no test exercising `..` sequences the way the branch tests do: [3](#0-2) [4](#0-3) 

Because `branch` is explicitly checked against the same `invalidCharacterRegex` that blocks `..+` sequences, the omission for `filepath` is not an oversight in the regex design — it is a parameter that was simply never routed through the guard that exists for its sibling. [5](#0-4) 

### Impact Explanation
This value is carried through `IOpenRepositoryFromURLAction` and dispatched by `Dispatcher.dispatchURLAction` -> `openRepositoryFromUrl`, which is invoked directly from an `x-github-client://openRepo/...` protocol link a user can click without any other interaction. If the consumer of `filepath` joins it with the freshly cloned repository's local path to open/reveal the file (as its purpose implies — "the file to open after cloning"), an attacker-controlled value like `filepath=../../../../etc/passwd` or a Windows UNC/drive-relative sequence would let a clicked link cause Desktop to open a file outside the cloned repository, i.e., a file read outside the repo triggered purely by a link click — squarely within the accepted impact category. [6](#0-5) 

### Likelihood Explanation
The attacker primitive matches the accepted threat model exactly: "a link or deep link the user clicks." No local access, prior malware, or leaked credentials are required — only a single click on a maliciously crafted `x-github-client://` (or platform-equivalent) URL, which is exactly how legitimate "Open in Desktop" buttons work on github.com and can be embedded anywhere (README, issue, forum post, chat).

### Recommendation
Apply the same defensive check used for `branch` to `filepath`: reject or sanitize any `filepath` value containing `..`, absolute-path indicators, or path separators outside the expected relative-path shape (e.g., normalize the joined path and assert it resides within the repository root before use), mirroring `testForInvalidChars`/`sanitizedRefName`.

### Proof of Concept
```
x-github-client://openRepo/https://github.com/attacker/repo?filepath=..%2F..%2F..%2F..%2FLibrary%2FApplication%20Support%2FGitHub%20Desktop%2Fcredentials.json
```
`parseAppURL` will return this untouched as `filepath: "../../../../Library/Application Support/GitHub Desktop/credentials.json"` (per the parsing logic and existing tests showing raw pass-through), unlike an equivalent `branch=..%2F..` payload which is rejected by `testForInvalidChars`. [1](#0-0) 

**Caveat:** I was not able to fully trace the exact file-open sink inside `dispatcher.ts`/`openRepositoryFromUrl` (index size limits prevented retrieving that specific code block), so I cannot confirm with certainty whether the consumer performs its own path-containment check downstream. If you need that confirmed, a full session with file access would be needed to inspect `app/src/ui/dispatcher/dispatcher.ts` around the `filepath` usages and any `AppStore` methods that open the file.

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

**File:** app/src/lib/parse-app-url.ts (L98-124)
```typescript
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
```

**File:** app/test/unit/parse-app-url-test.ts (L72-78)
```typescript
    it('returns unknown for invalid branch name', () => {
      // branch=<>
      const result = parseAppURL(
        'github-mac://openRepo/https://github.com/octokit/octokit.net?branch=%3C%3E'
      )
      assert.equal(result.name, 'unknown')
    })
```

**File:** app/test/unit/parse-app-url-test.ts (L80-93)
```typescript
    it('adds file path if found', () => {
      const result = parseAppURL(
        'github-mac://openRepo/https://github.com/octokit/octokit.net?branch=master&filepath=Octokit.Reactive%2FOctokit.Reactive.csproj'
      )
      assert.equal(result.name, 'open-repository-from-url')

      const openRepo = result as IOpenRepositoryFromURLAction
      assert.equal(openRepo.url, 'https://github.com/octokit/octokit.net')
      assert.equal(openRepo.branch, 'master')
      assert.equal(
        openRepo.filepath,
        'Octokit.Reactive/Octokit.Reactive.csproj'
      )
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2100-2131)
```typescript
  public async dispatchURLAction(action: URLActionType): Promise<void> {
    switch (action.name) {
      case 'oauth':
        await this.appStore._resolveOAuthRequest(action)

        if (__DARWIN__) {
          // workaround for user reports that the application doesn't receive focus
          // after completing the OAuth signin in the browser
          const isFocused = await isWindowFocused()
          if (!isFocused) {
            log.info(
              `refocusing the main window after the OAuth flow is completed`
            )
            window.focus()
          }
        }
        break

      case 'open-repository-from-url':
        this.openRepositoryFromUrl(action)
        break

      default:
        const unknownAction: IUnknownAction = action
        log.warn(
          `Unknown URL action: ${
            unknownAction.name
          } - payload: ${JSON.stringify(unknownAction)}`
        )
    }
  }

```
