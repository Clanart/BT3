Based on my research, I found a real analog in the GitHub Desktop code that matches the reported bug class: a validation check is applied to one field but not to a sibling field that flows through the same code path and is used similarly downstream.

### Title
Inconsistent validation between `branch` and `filepath` in deep-link `openRepo` handler allows unsanitized attacker-controlled path - (File: app/src/lib/parse-app-url.ts)

### Summary
`parseAppURL` handles the `x-github-client://openRepo/...` (and `github-mac://openRepo/...`) deep-link action. It extracts `pr`, `branch`, and `filepath` from the query string, but only `branch` (and `pr`, via a strict `^\d+$` regex) are checked for invalid/dangerous characters before being returned. `filepath` is passed straight through with no equivalent check, even though it is handled in the same block and follows the exact same source (an external, attacker-controlled link).

### Finding Description
In `parseAppURL` [1](#0-0) , the function reads `pr`, `branch`, and `filepath` from the query string of a URL that originates from outside the app (a deep link a user clicks, or a `--protocol-launcher` argument handled in `handleCommandLineArguments` [2](#0-1) ). It validates `pr` with a numeric regex and validates `branch` with `testForInvalidChars`, which rejects control characters, `~^:?*[\|"<>`, `@{`, consecutive dots, leading/trailing dots, and `.lock`/trailing-slash suffixes [3](#0-2) . However, `filepath` receives no such check at all — it is read from the query string and returned unmodified as part of the `open-repository-from-url` action [4](#0-3) .

This is structurally the same class of bug as the reported issue: a value that should be validated for a given purpose (path/ref safety) is either checked against the wrong criteria or not checked consistently with a sibling value that shares the same threat model. Here, `branch` is defended against ref-injection characters and traversal-like patterns (`..`, leading `.`), while `filepath` — which is documented and tested to represent a path within the repo (e.g. `Octokit.Reactive/Octokit.Reactive.csproj`, per the test at `app/test/unit/parse-app-url-test.ts:80-93`) — has no defense against sequences like `../../` or absolute path prefixes.

### Impact Explanation
If `filepath` is later joined with the local repository path to open/reveal a file (its naming and existing usages in `dispatcher.ts` and `main.ts` strongly suggest this — see the `filepath` references in `app/src/ui/dispatcher/dispatcher.ts` and `app/src/main-process/main.ts`), an attacker who controls a link (e.g., an external site, chat message, or malicious GitHub-hosted content) could craft a `x-github-client://openRepo/<url>?filepath=../../../../some/sensitive/file` deep link. Because there is no equivalent of `testForInvalidChars` or path-normalization/containment check on `filepath`, this could result in the app opening or revealing a file outside the intended repository directory when the user clicks the link — matching the "file read outside the repo" impact class from a link the user clicks.

I was not able to fully trace, within the available tool budget, the exact downstream code that consumes `filepath` in `dispatcher.ts` to confirm whether it performs its own path-containment check before opening the file. This is the key remaining uncertainty: if `dispatcher.ts` re-validates or resolves `filepath` safely (e.g. rejects paths that escape the repo root), the practical impact would be mitigated even though the `parse-app-url.ts` validation itself remains inconsistent.

### Likelihood Explanation
The entry point is a standard OS-level protocol handler that GitHub Desktop registers (`x-github-client://`, `github-mac://`), reachable via any link a user clicks (web page, email, chat) without any other privilege — this matches the "link or deep link the user clicks" attacker primitive. The missing check is easy to trigger (just a crafted query string) and the asymmetry with `branch`'s validation is clear evidence that `filepath` was likely intended to be constrained similarly but was overlooked, mirroring the "wrong/missing variable validated" pattern from the source report.

### Recommendation
Apply the same (or a stricter, path-traversal-specific) validation to `filepath` as is applied to `branch` in `parseAppURL`, and additionally ensure any downstream consumer that joins `filepath` with the repository root resolves and confirms the final path stays within the repository directory before performing any file open/read operation.

### Proof of Concept
1. Host or send a link: `x-github-client://openRepo/https://github.com/some/repo?branch=master&filepath=..%2F..%2F..%2F..%2Fetc%2Fpasswd` (or a Windows-relevant traversal target).
2. User clicks the link; `parseAppURL` returns `{ name: 'open-repository-from-url', url, branch: 'master', filepath: '../../../../etc/passwd', pr: null }` with no rejection, since `filepath` is never passed to `testForInvalidChars` (confirmed by reading [1](#0-0) ).
3. Whatever handler in `dispatcher.ts`/`main.ts` consumes `filepath` to open the file would need to be checked to confirm it does not independently sanitize/contain the path — this step is the part I could not fully verify given the remaining tool budget.

**Note on confidence**: this finding is based on a clearly confirmed inconsistency in `parse-app-url.ts` (validated `branch` vs. unvalidated `filepath`) but the full exploit chain through `dispatcher.ts`'s consumption of `filepath` was not fully traced before running out of available tool calls. A Devin session with full file access should verify the exact `filepath` consumer code before treating this as a confirmed, exploitable path-traversal bug rather than a validation-inconsistency issue.

### Citations

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

**File:** app/src/main-process/main.ts (L238-280)
```typescript
async function handleCommandLineArguments(argv: string[]) {
  const args = parseCommandLineArgs(argv, {
    boolean: ['protocol-launcher'],
  })

  // Desktop registers it's protocol handler callback on Windows as
  // `[executable path] --protocol-launcher "%1"`. Note that extra command
  // line arguments might be added by Chromium
  // (https://electronjs.org/docs/api/app#event-second-instance).

  if (__WIN32__ && args['protocol-launcher'] === true) {
    // On Windows we'll end up getting called with something like
    // `--protocol-launcher --allow-file-access-from-files x-github-client://..`
    // which minimist naturally interprets as
    // `--allow-file-access-from-files=x:/github-client`. This is due to
    // Chromium's hot take on parsing command line arguments, see:
    // https://github.com/electron/electron/issues/20322#issuecomment-534137321
    // So while we could add '--allow-file...' as a boolean we can't know for
    // sure that Chromium won't add more switches later on which is why we have
    // to resort to looking through all arguments looking for something that
    // appears to be an app url.
    const prefixes = Array.from(possibleProtocols, p => `${p}://`)
    const matchingUrl = argv.find(arg => {
      if (prefixes.some(p => arg.startsWith(p))) {
        try {
          new URL(arg)
          return true
        } catch (e) {
          log.error(`Unable to parse argument as URL: ${arg}`)
        }
      }
      return false
    })

    if (matchingUrl) {
      handleAppURL(matchingUrl)
    } else {
      log.error(`Encountered --protocol-launcher without app url`)
    }
    // If --protocol-launcher is present we always want to bail and not
    // risk a smuggled cli switch
    return
  }
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
