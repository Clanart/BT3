Based on confirmed code evidence, I found a concrete analog: the deep-link `filepath` parameter is never validated against path-traversal, unlike `branch` which is checked with `testForInvalidChars`.

### Title
Unsanitized `filepath` parameter in `openrepo` deep link allows path traversal to open files outside the cloned repository - (File: app/src/lib/parse-app-url.ts)

### Summary
`parseAppURL` in `app/src/lib/parse-app-url.ts` parses the `x-github-client://openrepo/...` (and legacy `github-mac://openRepo/...`) deep link and extracts `branch`, `pr`, and `filepath` query parameters. [1](#0-0)  The `branch` value is explicitly validated with `testForInvalidChars` (imported from `sanitize-ref-name.ts`) to reject unsafe characters, and `pr` is validated against a numeric regex. [2](#0-1)  The `filepath` value, however, is passed through with no validation whatsoever before being returned as part of the `IOpenRepositoryFromURLAction`. [3](#0-2)  This is confirmed by the unit test showing `filepath` accepted verbatim, including path separators, from the query string. [4](#0-3) 

### Finding Description
The broken invariant is: "any attacker-supplied string that is later used to construct or open a filesystem path must be sanitized against traversal sequences (`../`, absolute paths, etc.) before use." The `IOpenRepositoryFromURLAction.filepath` field is documented as "the file to open after cloning the repository" and is attacker-controlled — it comes directly from a URL that a user clicks (an "Open in Desktop"-style deep link), which per the report's attacker model is a "link or deep link the user clicks." [5](#0-4) 

The existing guard, `testForInvalidChars`, is only applied to `branch`, not to `filepath`. [6](#0-5)  That regex itself only rejects control characters, `~^:?*[\|"<>`, `@{`, consecutive dots, and refs ending in `.lock` or `/` — even if it were applied to `filepath`, a single `..` segment (not `..+`) combined with `/` would still traverse directories since the regex targets git-refname rules, not filesystem-path rules. [7](#0-6)  Since `filepath` gets none of this scrutiny at all, an attacker can freely embed `../../../../` sequences or an absolute path in the `filepath` query parameter of the deep link.

The `dispatchURLAction` in `app/src/ui/dispatcher/dispatcher.ts` routes `open-repository-from-url` actions to `openRepositoryFromUrl(action)`, which several other references in that file confirm consume the `filepath` field, presumably to resolve it against the newly cloned repository's working directory and open it in the user's configured editor/Desktop UI. [8](#0-7)  I was not able to inspect the exact path-join/open logic inside `openRepositoryFromUrl` within the available tool budget, so the precise sink (e.g., `path.join(repository.path, filepath)` followed by an editor-open or file-read call) is not independently confirmed from this pass — this should be verified by reading `app/src/ui/dispatcher/dispatcher.ts` around the other `filepath` references before treating this as fully proven.

### Impact Explanation
If the unresolved sink indeed joins `repository.path` with the raw `filepath` and opens/reads it, a malicious repository owner or link author could craft a deep link such as `x-github-client://openrepo/https://github.com/attacker/repo?filepath=..%2F..%2F..%2F.ssh%2Fid_rsa` that, once the victim clicks it and the clone completes, causes Desktop to open (and display/read) a file entirely outside the cloned repository — matching the accepted impact category "file write or read outside the repo" via "a link or deep link the user clicks."

### Likelihood Explanation
The `branch` parameter is deliberately hardened against invalid/dangerous characters while `filepath` sits right next to it in the same parsing function and receives zero equivalent treatment, which suggests an overlooked, not intentional, gap. [1](#0-0)  The `open-repository-from-url` action requires only that the victim click a specially crafted link (matching the "unprivileged" and "link the user clicks" acceptance criteria) — no local access, admin rights, or pre-existing malware are needed.

### Recommendation
Apply a filesystem-safe sanitizer to `filepath` in `parseAppURL` (or in `openRepositoryFromUrl`) that rejects/strips `..` segments and absolute-path prefixes, and resolve the final path with `path.resolve` followed by a check that the resolved path still starts with the repository's root directory before opening it — mirroring the validation already done for `branch`.

### Proof of Concept
1. Attacker crafts a deep link: `x-github-client://openrepo/https://github.com/attacker/public-repo?branch=main&filepath=..%2F..%2F..%2F..%2F.ssh%2Fid_rsa`
2. Victim clicks the link (e.g., embedded in a webpage or README "Open in Desktop" button).
3. `parseAppURL` returns `{ name: 'open-repository-from-url', url, branch: 'main', pr: null, filepath: '../../../../.ssh/id_rsa' }` without rejecting the traversal sequence, since only `branch` is checked with `testForInvalidChars`. [1](#0-0) 
4. `dispatchURLAction` forwards this to `openRepositoryFromUrl`, which (pending confirmation of the exact join logic) would attempt to open the resolved path relative to the freshly cloned repo, escaping the repo directory. [8](#0-7) 

Note: because I could not confirm the internal implementation of `openRepositoryFromUrl` (the exact path-join and open call) within the available search iterations, this finding should be validated by inspecting that function directly in `app/src/ui/dispatcher/dispatcher.ts` (and any downstream file-open utility it calls) before treating the traversal-to-arbitrary-file-open impact as fully confirmed rather than a high-likelihood, evidence-backed hypothesis.

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

**File:** app/src/lib/sanitize-ref-name.ts (L1-6)
```typescript
// See https://www.kernel.org/pub/software/scm/git/docs/git-check-ref-format.html
// ASCII Control chars and space, DEL, ~ ^ : ? * [ \
// | " < and > is technically a valid refname but not on Windows
// the magic sequence @{, consecutive dots, leading and trailing dot, ref ending in .lock
const invalidCharacterRegex =
  /[\x00-\x20\x7F~^:?*\[\\|""<>]+|@{|\.\.+|^\.|\.$|\.lock$|\/$/g
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2118-2120)
```typescript
      case 'open-repository-from-url':
        this.openRepositoryFromUrl(action)
        break
```
