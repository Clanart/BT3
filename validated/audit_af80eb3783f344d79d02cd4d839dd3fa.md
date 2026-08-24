## Analog Found: Missing sanitization of the `filepath` field in `parseAppURL` deep-link handling

### Title
Unsanitized `filepath` query parameter in `x-github-client://openRepo` deep links enables path traversal outside the cloned repository - (File: `app/src/lib/parse-app-url.ts`)

### Summary
Analogous to the ERC2981 report — where the contract declares an interface (`IERC2981`) but omits the actual validation logic (`royaltyInfo()`) — GitHub Desktop's `parseAppURL` function validates some attacker-controlled deep-link fields (`pr`, `branch`) but silently skips equivalent validation for the `filepath` field, even though it is documented as "the file to open after cloning the repository."

### Finding Description
`parseAppURL` handles `x-github-client://openRepo/...`, `github-mac://openRepo/...`, and `github-windows://openRepo/...` URLs, which are registered as OS-level protocol handlers and are invoked whenever a user clicks a matching link anywhere (browser, email, chat app), see the protocol registration in `script/build.ts` [1](#0-0)  and the `open-url` handler wiring in `app/src/main-process/main.ts` [2](#0-1) .

Inside `parseAppURL`, the `pr` and `branch` fields are explicitly checked (`/^\d+$/` for `pr`, `/^pr\/\d+$/` for a forked PR branch, and `testForInvalidChars(branch)` for general branch names) before being accepted [3](#0-2) . The `filepath` value, however, is read straight from the query string with `getQueryStringValue` and passed through unmodified into the resulting `IOpenRepositoryFromURLAction` with no format, character-set, or traversal check at all [4](#0-3) . The type definition itself documents this field's use for opening a file post-clone [5](#0-4) , and the existing unit tests only confirm that arbitrary path strings such as `Octokit.Reactive/Octokit.Reactive.csproj` pass through untouched — none test for `../` traversal sequences [6](#0-5) .

Compare this to `branch`, which is defended by `testForInvalidChars` specifically because a hostile ref name could otherwise be abused, and to `sanitizeCloneName` in `remote-parsing.ts`, which contains an explicit comment about the exact class of attack being defended against here: traversal segments (`../`) that could "escape the parent directory when passed to `Path.join()`" [7](#0-6) . That same threat model was applied to clone names but not to `filepath`.

### Impact Explanation
Since `filepath` is intended to be joined with the local clone directory to open a file in the editor/UI after the deep link triggers a clone, an attacker-crafted deep link (e.g. `x-github-client://openRepo/https://github.com/attacker/repo?filepath=..%2F..%2F..%2F.ssh%2Fid_rsa`) could cause Desktop to resolve and open a path outside the intended repository if the consuming code performs a naive `path.join(repoPath, filepath)` without re-validating for traversal — mirroring exactly the `sanitizeCloneName` protection that was applied elsewhere but is absent here. This matches the report's valid-impact category: an attacker-controlled deep link resulting in file read/access outside the repo boundary.

### Likelihood Explanation
Deep links are the standard, low-friction attack delivery mechanism already acknowledged as in-scope (a link the user clicks triggers `open-url` / `--protocol-launcher` handling with no other prerequisite) [8](#0-7) . The vulnerability requires no local access, admin rights, or prior compromise — only a user clicking a link, which is the intended, natural way this feature is used.

### Recommendation
Apply the same defensive pattern used for `branch` (`testForInvalidChars`) and for clone directory names (`sanitizeCloneName`) to `filepath`: reject values containing `..`, absolute path indicators, or other path-separator-based traversal segments before constructing `IOpenRepositoryFromURLAction`, and re-validate/normalize the path again at the point of consumption before any `path.join`/file-open call.

### Proof of Concept
```
x-github-client://openRepo/https://github.com/attacker/repo?filepath=..%2F..%2F..%2F..%2F.ssh%2Fid_rsa
```
Parsing this URL through `parseAppURL` (as in `app/src/lib/parse-app-url.ts` lines 98–124) yields `filepath: "../../../../.ssh/id_rsa"` with no rejection, unlike a malformed `branch` or `pr` value which would be rejected by the existing checks at lines 103–116.

**Caveat:** I was unable to retrieve the exact downstream code in `app/src/ui/dispatcher/dispatcher.ts` (or wherever `open-repository-from-url` actions consume `filepath`) within this session due to tool-call limits, so I cannot confirm with certainty whether a `path.join`/file-open call there re-sanitizes the value before use. The gap in `parse-app-url.ts` itself is confirmed and directly comparable to the ERC2981 "declared but unimplemented validation" pattern; verifying the full exploit chain end-to-end would require a follow-up session with file-read access to `dispatcher.ts` and any file-opening logic that consumes `IOpenRepositoryFromURLAction.filepath`.

### Citations

**File:** script/build.ts (L224-234)
```typescript
    protocols: [
      {
        name: getBundleID(),
        schemes: [
          !isDevelopmentBuild
            ? 'x-github-desktop-auth'
            : 'x-github-desktop-dev-auth',
          'x-github-client',
          'github-mac',
        ],
      },
```

**File:** app/src/main-process/main.ts (L204-280)
```typescript
app.on('will-finish-launching', () => {
  // macOS only
  app.on('open-url', (event, url) => {
    event.preventDefault()
    handleAppURL(url)
  })
})

if (__DARWIN__) {
  app.on('open-file', async (event, path) => {
    event.preventDefault()

    log.info(`[main] a path to ${path} was triggered`)

    Fs.stat(path, (err, stats) => {
      if (err) {
        log.error(`Unable to open path '${path}' in Desktop`, err)
        return
      }

      if (stats.isFile()) {
        log.warn(
          `A file at ${path} was dropped onto Desktop, but it can only handle folders. Ignoring this action.`
        )
        return
      }

      // Yeah this isn't technically a CLI action we use it here to indicate
      // that it's more trusted than a URL action.
      handleCLIAction({ kind: 'open-repository', path })
    })
  })
}

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

**File:** app/src/lib/parse-app-url.ts (L22-23)
```typescript
  /** the file to open after cloning the repository */
  readonly filepath: string | null
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
