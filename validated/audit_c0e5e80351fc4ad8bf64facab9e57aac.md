Based on the investigation, the strongest analog to the reported "unvalidated state -> attacker-reachable dangerous path" bug class is a **git-URL scheme injection** in GitHub Desktop's deep-link "Open in Desktop" flow, where an attacker-controlled URL string can reach `git clone`/`git remote add` unsanitized and trigger execution through Git's `ext::` (or similar) transport helper.

Note: I was unable to fully inspect `cloningRepositoriesStore.clone()` (not indexed in the available snippets), so I cannot 100% confirm whether an additional scheme allowlist exists between `dispatcher.clone()` and the actual `git clone` invocation. This should be verified directly in `app/src/lib/stores/cloning-repositories-store.ts` before treating this as fully confirmed.

### Title
Deep-link "Open in Desktop" URL reaches `git clone`/`git remote add` without scheme validation, enabling `ext::`-transport command execution - (File: app/src/ui/clone-repository/clone-repository.tsx / app/src/lib/git/remote.ts)

### Summary
GitHub Desktop registers `x-github-client://` (and platform variants) as its protocol handler. `parseAppURL` extracts the raw path segment from an `openRepo` deep link and returns it verbatim as the "clone URL" for the `open-repository-from-url` action, with no restriction on URL scheme. When Desktop cannot resolve this string to a known GitHub owner/repo via the API, `resolveCloneInfo` in the Clone dialog falls back to using the literal, attacker-supplied string as the clone URL, which is then passed straight into the clone pipeline and ultimately into a `git` invocation. Git supports non-network transports such as `ext::<command>` that execute an arbitrary shell command as part of the "clone", so a crafted deep link can achieve code execution when the user clicks it (and confirms/initiates the resulting Clone dialog).

### Finding Description
The parsing entry point is `parseAppURL`: [1](#0-0) 

Note that only `pr`/`branch`/`filepath` are validated with regexes; the `url` field itself (`parsedPath`) is never checked against a scheme allowlist (e.g., restricted to `https:`/`ssh:`/`git:`).

This action is dispatched to `openRepositoryFromUrl` → `openOrCloneRepository`, which — when no existing local repository matches — opens the generic Clone dialog with the raw URL as `initialURL`: [2](#0-1) 

In the Clone dialog, `resolveCloneInfo` tries to resolve the string via the GitHub API, but falls back to returning the literal URL unchanged whenever no account/API match is found (e.g., because the string doesn't look like a normal git remote or `owner/name` alias): [3](#0-2) 

That value is then handed to `cloneImpl` → `dispatcher.clone(url, path, ...)` → `appStore._clone` → `cloningRepositoriesStore.clone(url, path, options)`, which ultimately shells out to Git. Separately, the same trust pattern shows up in the pull-request-from-fork flow, where `pullRequest.head.repo.clone_url` (a field from a GitHub-API-shaped object) is passed unchecked into `addRemote`, which builds the argv directly: [4](#0-3) [5](#0-4) 

Git itself treats a URL of the form `ext::<shell-command>` (and similarly `fd::`, or file-based tricks) as an instruction to execute the given command as the "transport" for the clone/fetch, rather than as a network address. None of the code paths above restrict the accepted scheme before the string reaches Git, unlike the clone-path handling in the same file, which *was* hardened against path traversal via `sanitizeCloneName`: [6](#0-5) 

That existing fix demonstrates the project is aware that link/URL-derived strings need sanitization before being used in dangerous operations — but the sanitization was applied only to the *directory name* derivation, not to the *URL scheme itself* before it is fed to `git clone`/`git remote add`.

### Impact Explanation
If the fallback path is reachable with an untouched, attacker-crafted string, clicking a single malicious deep link (`x-github-client://openRepo/ext::sh%20-c%20calc` or similar, URL-decoded to `ext::sh -c calc`) can result in **arbitrary command execution** on the victim's machine as soon as GitHub Desktop performs the "clone" using that string as the Git URL. This satisfies the report's severity class (unprivileged, attacker fully controls the primitive — here, a link the user clicks — and impact is code execution), analogous to the original report's "funds locked because the code trusted an assumed precondition (vesting already started) that wasn't actually validated."

### Likelihood Explanation
- The deep-link protocol handler is registered and reachable without any prior authentication (`app.on('open-url', ...)` in the main process, or `--protocol-launcher` on Windows): [7](#0-6) 
- Triggering only requires the victim to click a link (an ordinary user action, not "unnatural" or requiring local/admin access).
- The main open question (and reason I flag this as needing verification rather than 100% confirmed) is whether `cloningRepositoriesStore.clone()` or the underlying `git clone` wrapper (`app/src/lib/git/clone.ts`, not available in the indexed context) performs any scheme/prefix validation before invoking `git`. If it does not, likelihood is high given the direct, unauthenticated reachability from a deep link.

### Recommendation
- In `parseAppURL` (`app/src/lib/parse-app-url.ts`), reject `open-repository-from-url` actions whose `url` does not match an allow-listed set of schemes (`https:`, `http:`, `ssh:`, or a bare `git@host:owner/repo` / `owner/repo` shorthand), mirroring the same defensive posture already applied to `branch` via `testForInvalidChars`.
- In `resolveCloneInfo` (`app/src/ui/clone-repository/clone-repository.tsx`), do not fall back to using the raw, unresolved URL string for cloning; instead, require the string to pass `parseRemote`/`parseRepositoryIdentifier` validation before being handed to the clone pipeline.
- In `addRemote` (`app/src/lib/git/remote.ts`) and everywhere a GitHub-API-derived `clone_url` is used (`_checkoutPullRequest`/`_findPullRequestBranch` in `app/src/lib/stores/app-store.ts`), validate the URL scheme against an allowlist before calling `git remote add`/`git fetch`, and consider explicitly passing `-c protocol.ext.allow=never -c protocol.file.allow=never` (or the modern equivalents) for any operation on API/URL-derived remotes.

### Proof of Concept
1. Register/observe that GitHub Desktop handles `x-github-client://` links (macOS `open-url` handler / Windows `--protocol-launcher`).
2. Host or send the victim a link:
   `x-github-client://openRepo/ext::sh%20-c%20%22touch%20/tmp/pwned%22`
3. Victim clicks the link. GitHub Desktop's `handleAppURL` → `parseAppURL` extracts `url = "ext::sh -c \"touch /tmp/pwned\""` and dispatches `open-repository-from-url`.
4. `openOrCloneRepository` opens the Clone dialog with this string as `initialURL`; `resolveCloneInfo` cannot resolve it via the API and falls back to `{ url }` unchanged.
5. On clicking "Clone", the raw string is passed to the clone pipeline and ultimately to `git clone`, where Git's `ext::` transport executes `sh -c "touch /tmp/pwned"` on the victim's machine.

(Step 5 depends on the unverified internals of `cloningRepositoriesStore.clone()`/`app/src/lib/git/clone.ts` not filtering the scheme — this should be confirmed against the actual source before treating the PoC as fully validated.)

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2215-2233)
```typescript
  private async openOrCloneRepository(url: string): Promise<Repository | null> {
    const state = this.appStore.getState()
    const repositories = state.repositories
    const existingRepository = repositories.find(r =>
      this.doesRepositoryMatchUrl(r, url)
    )

    if (existingRepository) {
      return await this.selectRepository(existingRepository)
    }

    return this.appStore._startOpenInDesktop(() => {
      this.changeCloneRepositoriesTab(CloneRepositoryTab.Generic)
      this.showPopup({
        type: PopupType.CloneRepository,
        initialURL: url,
      })
    })
  }
```

**File:** app/src/ui/clone-repository/clone-repository.tsx (L732-753)
```typescript
  private async resolveCloneInfo(): Promise<IAPIRepositoryCloneInfo | null> {
    const { url, lastParsedIdentifier } = this.getSelectedTabState()

    if (url.endsWith('.wiki.git')) {
      return { url }
    }

    const account = await findAccountForRemoteURL(url, this.props.accounts)
    if (lastParsedIdentifier !== null && account !== null) {
      const api = API.fromAccount(account)
      const { owner, name } = lastParsedIdentifier
      // Respect the user's preference if they provided an SSH URL
      const protocol = parseRemote(url)?.protocol

      return api.fetchRepositoryCloneInfo(owner, name, protocol).catch(err => {
        log.error(`Failed to look up repository clone info for '${url}'`, err)
        return { url }
      })
    }

    return { url }
  }
```

**File:** app/src/lib/git/remote.ts (L28-37)
```typescript
/** Add a new remote with the given URL. */
export async function addRemote(
  repository: Repository,
  name: string,
  url: string
): Promise<IRemote> {
  await git(['remote', 'add', name, url], repository.path, 'addRemote')

  return { url, name }
}
```

**File:** app/src/lib/stores/app-store.ts (L8640-8660)
```typescript
    const gitStore = this.gitStoreCache.get(repository)
    const remotes = await getRemotes(repository)

    // Find an existing remote (regardless if set up by us or outside of
    // Desktop).
    let remote = remotes.find(r => urlMatchesRemote(headCloneUrl, r))

    // If we can't find one we'll create a Desktop fork remote.
    if (remote === undefined) {
      try {
        const forkRemoteName = forkPullRequestRemoteName(headRepoOwner)
        remote = await addRemote(repository, forkRemoteName, headCloneUrl)
      } catch (e) {
        this.emitError(
          new Error(
            `Couldn't find PR branch, adding remote failed: ${e.message}`
          )
        )
        return
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
