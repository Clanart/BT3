## Finding: `filepath` deep-link parameter is not sanitized by `parseAppURL` (unlike `branch`) - (File: `app/src/lib/parse-app-url.ts`)

### Summary
In `parseAppURL`, the `branch` query parameter for `openrepo` deep links is validated with `testForInvalidChars` before being returned, but the `filepath` parameter is extracted with `getQueryStringValue` and passed straight through into the `IOpenRepositoryFromURLAction` object with no validation whatsoever.

### Finding Description
`parseAppURL` handles the `openrepo` deep-link action as follows: [1](#0-0) 

Note the asymmetry: `branch` is checked with `if (branch != null && testForInvalidChars(branch)) { return unknown }` (line 114), but `filepath` (extracted at line 101) has no equivalent check before being placed into the returned action object at line 123. This confirms the proof idea in the question — a URL such as `github-mac://openRepo/<url>?filepath=../../secret` will have `parsedURL.query.filepath` returned unmodified as `../../secret` in the `IOpenRepositoryFromURLAction.filepath` field. [2](#0-1) 

I was not able to fully trace, within the available tooling, the exact downstream sink where `IOpenRepositoryFromURLAction.filepath` is consumed after being dispatched (e.g., whether it's joined with the cloned repository's root path and passed to a file-open/reveal API, or how `dispatcher.ts` uses it — a search showed 6 references to `filepath` in `app/src/ui/dispatcher/dispatcher.ts` but I could not retrieve their content before running out of iterations). Because of this, I cannot confirm with certainty that this unsanitized value actually reaches a file-read/open operation capable of escaping the repository root (i.e., that path-joining/normalization doesn't happen at the consumption site, and that no OS/Electron-level containment exists there).

### Impact Explanation
If the downstream consumer (post-clone "open file" flow) naively joins `TARGET_PATH` (the cloned repo root) with the unsanitized `filepath` value and passes it to a file-open API, a value like `../../.ssh/id_rsa` could cause Desktop to open/read a file outside the intended repository directory — a file-read-outside-repo condition, matching the bounty's "file... read outside the repo" impact category. However, this impact is contingent on the sink not performing its own path containment check, which I could not verify.

### Likelihood Explanation
The parser-level gap is confirmed and easily reproducible (parseAppURL returns the raw string). The overall exploitability depends entirely on the unverified sink behavior in the "open file after clone" code path.

### Recommendation
Regardless of sink behavior, defense-in-depth suggests: (1) sanitize/validate `filepath` in `parseAppURL` similarly to `branch` (e.g., reject values containing `..`, absolute paths, or path separators pointing outside expected bounds), and (2) ensure any consumer that joins `filepath` with the repository root resolves the final path and asserts it remains within the repository root before opening/reading it.

### Proof of Concept
```
github-mac://openRepo/https%3A%2F%2Fgithub.com%2Fowner%2Frepo?filepath=../../secret
```
Calling `parseAppURL` on this URL returns:
```js
{
  name: 'open-repository-from-url',
  url: 'https://github.com/owner/repo',
  branch: null,
  pr: null,
  filepath: '../../secret'
}
```
confirmed directly by reading the source of `parseAppURL` at [1](#0-0) .

**Caveat:** This finding validates the specific claim asked about `parseAppURL` — it does not apply the same traversal/invalid-char check to `filepath` as it does to `branch`, and returns it unmodified. I was unable to confirm within available tool calls whether the downstream `open-repository-from-url` consumption in `dispatcher.ts` performs its own path-containment check before opening the file; if it does (e.g., via `path.resolve` + prefix check, or an editor API that itself refuses to escape a working directory), the end-to-end file-read-outside-repo impact would not materialize despite the parser-level gap. I recommend a follow-up session with full file-read access to `app/src/ui/dispatcher/dispatcher.ts` to confirm the sink behavior before treating this as a fully proven end-to-end vulnerability.

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

**File:** app/src/lib/parse-app-url.ts (L98-125)
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
  }
```
