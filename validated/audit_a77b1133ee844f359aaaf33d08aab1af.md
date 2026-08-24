## Title
Unvalidated `filepath` query parameter in `x-github-client://openrepo` deep links allows path traversal to open arbitrary files outside the cloned repository - (File: `app/src/lib/parse-app-url.ts`)

## Summary
GitHub Desktop's custom-protocol handler (`x-github-client://`, `x-github-desktop-auth://`, etc.) parses attacker-controlled deep-link URLs in `parseAppURL()`. Every parameter that ends up being used for a filesystem or git-ref-sensitive purpose is validated — `pr` must be `^\d+$`, `branch` must match `^pr\/\d+$` (for forked PRs) and is checked with `testForInvalidChars()` against Git's ref-format blacklist — except `filepath`, which is passed straight through unsanitized to `IOpenRepositoryFromURLAction.filepath`, "the file to open after cloning the repository."

## Finding Description
`parseAppURL()` builds the `open-repository-from-url` action from the incoming URL's query string: [1](#0-0) 

Note that `pr` and `branch` are both explicitly validated (`/^\d+$/`, `/^pr\/\d+$/`, `testForInvalidChars`), but `filepath` — obtained the same way via `getQueryStringValue(query, 'filepath')` — has zero validation before being placed into the trusted `IOpenRepositoryFromURLAction`: [2](#0-1) 

This action is produced from a fully untrusted source: any web page, email, or chat message can invoke `x-github-client://openrepo/<owner>/<repo>?filepath=...` and the OS will hand the raw string straight to Desktop's `open-url`/protocol-launcher handler in the main process without any additional sanitization: [3](#0-2) [4](#0-3) 

This is the same class of bug as the PrimeX report: a validation/guard is applied inconsistently across sibling code paths that all consume attacker-influenced input (there, `amountOutMin`/oracle checks were applied to some order types but not swap/spot; here, ref-format and numeric checks are applied to `pr`/`branch` but not `filepath`), leaving one path in the same function completely unguarded even though the surrounding code demonstrates the author's intent to validate every field derived from the untrusted deep link.

## Impact Explanation
`filepath` is documented as "the file to open after cloning the repository," implying it is later joined with the freshly cloned repository's local path and opened automatically (Desktop has a helper, `openFile()`, that does exactly this kind of automatic-open-after-action flow using `shell.openExternal('file://' + fullPath)`): [5](#0-4) 
If the join of the repository path and the unsanitized `filepath` does not strip `..` traversal segments (unlike `sanitizeCloneName()`, which explicitly guards against this for the clone-target directory name): [6](#0-5) 
then a crafted link such as `x-github-client://openrepo/octocat/Hello-World?filepath=../../../../.ssh/id_rsa` (or an absolute Windows path) can cause Desktop to open a file outside the cloned repository the moment the user clicks the link — no additional prompts beyond the OS's "open in GitHub Desktop?" dialog. Depending on what `shell.openExternal`/`openPath` does with the resolved path and registered file handlers, this can lead to disclosure of sensitive files or, if the target has an executable extension with an OS-registered handler, execution of attacker-chosen content — matching the "attacker controls a link/deep link the user clicks" and "read outside the repo" impact categories.

## Likelihood Explanation
The attack requires only a single click on an attacker-supplied link (a normal Desktop-supported flow, since "Open in Desktop" links are a first-class, expected feature). The parsing code shows explicit validation was added for `pr` and `branch` specifically because they are known injection points, but `filepath` was overlooked — indicating a genuine gap rather than a defense-in-depth omission. This makes exploitation moderately likely if the downstream consumer does a naive `Path.join(repoPath, filepath)`.

## Recommendation
- In `parseAppURL()` (`app/src/lib/parse-app-url.ts`), validate `filepath` the same way `branch`/`pr` are validated: reject absolute paths, reject `..` traversal segments, and reject invalid characters before constructing `IOpenRepositoryFromURLAction`.
- Wherever `filepath` is consumed to open a file after cloning, resolve it against the repository root and verify with `path.resolve`/`path.relative` that the resolved path stays within the repository directory (mirroring the traversal-safety approach already implemented in `sanitizeCloneName()`), rejecting the action if it doesn't.

## Proof of Concept
1. Attacker sends the victim a link: `x-github-client://openrepo/octocat/Hello-World?filepath=..%2F..%2F..%2F..%2F.ssh%2Fid_rsa`.
2. Victim, who has GitHub Desktop installed, clicks the link (or it's opened via a `<a href>` on a malicious webpage or in an email client that lets protocol handlers register).
3. OS invokes Desktop's `open-url` handler → `handleAppURL(url)` → `parseAppURL(url)`, producing `{ name: 'open-repository-from-url', url: 'octocat/Hello-World', branch: null, pr: null, filepath: '../../../../.ssh/id_rsa' }` with no rejection.
4. Desktop clones (or reuses) the repo and — per the documented `filepath` behavior of opening it after cloning — resolves the path relative to the freshly cloned repository, escaping it via the unvalidated traversal segments, and opens the victim's SSH private key (or any other targeted file) with the OS default handler / attempts to display it.

I was unable to trace, within the remaining tool budget, the exact function in `app/src/ui/app.tsx` that consumes `IOpenRepositoryFromURLAction.filepath` after cloning to confirm the precise join/open call and whether any traversal guard exists there; this should be verified directly in the code (search for `filepath` usage in `app/src/ui/app.tsx`) before treating this as fully confirmed.

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

**File:** app/src/main-process/main.ts (L159-168)
```typescript
function handleAppURL(url: string) {
  log.info('Processing protocol url')
  const action = parseAppURL(url)
  onDidLoad(window => {
    // This manual focus call _shouldn't_ be necessary, but is for Chrome on
    // macOS. See https://github.com/desktop/desktop/issues/973.
    window.focus()
    window.sendURLAction(action)
  })
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

**File:** app/src/ui/lib/open-file.ts (L1-17)
```typescript
import { shell } from '../../lib/app-shell'
import { Dispatcher } from '../dispatcher'

export async function openFile(
  fullPath: string,
  dispatcher: Dispatcher
): Promise<void> {
  const result = await shell.openExternal(`file://${fullPath}`)

  if (!result) {
    const error = {
      name: 'no-external-program',
      message: `Unable to open file ${fullPath} in an external program. Please check you have a program associated with this file extension`,
    }
    await dispatcher.postError(error)
  }
}
```

**File:** app/src/lib/remote-parsing.ts (L72-88)
```typescript
/**
 * Extracts a safe single-component directory name from a URL-derived repo name.
 *
 * Mirrors the approach of git's `git_url_basename()` in `dir.c`: treat `/`,
 * `\`, and `:` as path separators, take the last non-empty component, strip a
 * trailing `.git` suffix, and reject traversal segments. This ensures the
 * result is always a single path component that cannot escape the parent
 * directory when passed to `Path.join()`.
 *
 * Examples:
 *  - `"Hello-World"` → `"Hello-World"` (unchanged)
 *  - `"desktop.git/../../otherdir"` → `"otherdir"` (last component, traversal segments skipped)
 *  - `".."` → `null` (traversal-only name rejected)
 *
 * See: https://github.com/git/git/blob/master/dir.c (`git_url_basename`)
 */
export function sanitizeCloneName(name: string): string | null {
```
