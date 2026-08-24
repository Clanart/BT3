### Title
Clone URL from untrusted GitHub deep links / CLI actions is passed to `git clone` without protocol allow-listing, enabling `ext::`-transport command execution - (File: `app/src/lib/git/clone.ts`)

### Summary
The oracle report's broken invariant is: **a single untrusted external input (a price feed) is trusted and acted on directly, with no validation of its content before being committed on-chain.** The Desktop analog is structurally identical: a single untrusted external input — a URL string coming from a `x-github-client://openRepo/<url>` deep link or the `github clone <url>` CLI entry point — is passed, unvalidated, straight into a `git clone` invocation. Git natively supports the `ext::<command>` remote-helper URL scheme, which executes an arbitrary shell command as the "transport." Desktop never checks the URL's scheme against an allow-list (`https`, `ssh`, `git`) before invoking `git`, so an attacker-controlled clone URL delivered via a link the user clicks can lead to command execution on the victim's machine.

### Finding Description
`parseAppURL` accepts an arbitrary path/URL for the `openrepo` deep-link action and forwards it unchanged as `open-repository-from-url`: [1](#0-0) 

`dispatcher.ts` routes this action, and the equivalent `--cli-clone=` CLI action, straight into `openOrCloneRepository(url)`, which only pre-fills the Clone dialog with the raw `url` (no protocol validation is performed here or in `dispatchCLIAction`): [2](#0-1) [3](#0-2) 

Once the user confirms cloning, `clone()` builds the git command line using the URL exactly as received — the only hardening present is a check on the destination *path* (`isClonePathSensitive`) and use of `--` to stop option/flag injection, but there is **no check on the URL's scheme**: [4](#0-3) 

`parseRemote`/`remote-parsing.ts` — the only URL-shape validation in the codebase — is used solely to extract `owner`/`name`/`hostname` for account matching and directory naming (`sanitizeCloneName`), and returns `null` for URLs that don't match its known GitHub-style regexes; that `null` result does **not** block the clone, it just means the folder name / account lookup falls back to defaults: [5](#0-4) [6](#0-5) 

Because git's remote-helper syntax (`ext::sh -c '<command>'`, `fd::<n>`) is treated by git itself as a valid "transport," and Desktop never restricts `GIT_ALLOW_PROTOCOL` / `protocol.*.allow` nor validates the scheme against `https|ssh|git`, a URL of this form reaches `git clone -- <url> <path>` unmodified and git will execute the embedded command.

This is the direct analog of the oracle bug: just as the price-feeder trusted a single provider's report without a sanity check before committing it on-chain, Desktop trusts a single external string (deep link / CLI argument) without a scheme sanity check before handing it to `git`, which treats it as a first-class, fully-privileged instruction.

### Impact Explanation
If exploitable end-to-end, this results in arbitrary code execution in the context of the desktop user (not sandboxed), which is one of the explicitly valid impacts (code execution via a link the user clicks). It's more severe than the oracle bug's "wrong balance": rather than corrupting a value, the attacker gains full command execution on the victim's OS account.

### Likelihood Explanation
Medium-to-low confidence/likelihood given what's visible in the code:
- The deep-link and CLI-clone paths only *pre-fill* the Clone dialog; the user must still click "Clone" to actually invoke `git clone`, which is a normal (not "unnatural") one-click confirmation step, similar to prior real-world reports against other git GUI clients accepting `ext::`/`fd::` URLs via "Clone" buttons.
- I could not find any explicit `GIT_ALLOW_PROTOCOL` environment variable or `protocol.ext.allow` configuration set anywhere in the environment/execution-options code (`envForRemoteOperation`, `executionOptionsWithProgress`), which would be the standard mitigation; this repo's index does not show one being applied.
- I was not able to confirm the exact bundled git version's default `protocol.ext.allow` behavior for a *top-level* `git clone` invocation (as opposed to a nested submodule/redirect fetch, where modern git already blocks `ext::` by default). Top-level `git clone` commands are typically treated by git as "user-initiated," which would make git's own default protections effectively moot regardless of where the URL string originated from (deep link vs. hand-typed). This nuance is a genuine limit of git's design, not unique to Desktop, but nothing in the codebase adds a layer on top of it to filter dangerous schemes before reaching git.

### Recommendation
- Add an explicit allow-list check on the clone/fetch/push/remote-add URL scheme in `app/src/lib/git/clone.ts` (and `app/src/lib/git/remote.ts`'s `addRemote`/`setRemoteURL`), rejecting anything that isn't `https:`, `http:`, `ssh:`, `git:`, or a local filesystem path, before the URL is ever handed to `git`.
- Explicitly set `GIT_ALLOW_PROTOCOL=http:https:ssh:git` (or the equivalent `-c protocol.*.allow=never` config flags) in `envForRemoteOperation` for all remote-facing git invocations (clone/fetch/push/ls-remote), as defense in depth independent of git's own defaults.
- Surface the parsed scheme/host in the Clone dialog and deep-link confirmation UI so a malicious `ext::`/`fd::` payload is visually obvious to the user before they click "Clone," rather than being buried in a URL string.

### Proof of Concept
1. Attacker crafts a link:
   `x-github-client://openRepo/ext::sh%20-c%20%22open%20-a%20Calculator%22`
2. Victim clicks the link. `parseAppURL` parses `hostname === 'openrepo'`, extracts `pathName` as the raw payload, and returns `{ name: 'open-repository-from-url', url: 'ext::sh -c "open -a Calculator"' }` (see `app/src/lib/parse-app-url.ts:98-124`).
3. `dispatcher.dispatchURLAction` → `openRepositoryFromUrl` → `openOrCloneRepository(url)` → opens the Clone dialog with `initialURL` set to the malicious string (`app/src/ui/dispatcher/dispatcher.ts:2215-2233`).
4. Victim clicks "Clone" (normal, expected interaction for the "Clone in Desktop" flow).
5. `dispatcher.clone(url, path, options)` → `appStore._clone` → `clone()` in `app/src/lib/git/clone.ts` executes `git ... clone --recursive --progress -- "ext::sh -c \"open -a Calculator\"" <path>`.
6. If the local git build honors `ext::` for a direct top-level clone (which is the git default for user-invoked clones), the embedded shell command executes.

Note: I could not fully verify the exact git version bundled with this Desktop build nor confirm end-to-end that `ext::` is not blocked by some git-level compile-time flag, since that information is outside the indexed source. This should be validated experimentally (attempt an actual `ext::`-URL clone through the UI) before treating this as a confirmed, exploitable vulnerability rather than a plausible analog.

### Citations

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2050-2058)
```typescript
  public async dispatchCLIAction(action: CLIAction) {
    if (action.kind === 'clone-url') {
      const { branch, url } = action

      if (branch) {
        await this.openBranchNameFromUrl(url, branch)
      } else {
        await this.openOrCloneRepository(url)
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

**File:** app/src/lib/git/clone.ts (L68-126)
```typescript
export async function clone(
  url: string,
  path: string,
  options: CloneOptions,
  progressCallback?: (progress: ICloneProgress) => void
): Promise<void> {
  if (isClonePathSensitive(path)) {
    throw new Error(
      `The clone destination "${path}" targets a sensitive system location. ` +
        'Cloning into this directory is not allowed.'
    )
  }

  const env = {
    ...(await envForRemoteOperation(url)),
    GIT_CLONE_PROTECTION_ACTIVE: 'false',
  }

  const defaultBranch = options.defaultBranch ?? (await getDefaultBranch())

  const args = [
    '-c',
    `init.defaultBranch=${defaultBranch}`,
    'clone',
    '--recursive',
  ]

  let opts: IGitStringExecutionOptions = { env }

  if (progressCallback) {
    args.push('--progress')

    const title = `Cloning into ${path}`
    const kind = 'clone'

    opts = await executionOptionsWithProgress(
      { ...opts, trackLFSProgress: true },
      new CloneProgressParser(),
      progress => {
        const description =
          progress.kind === 'progress' ? progress.details.text : progress.text
        const value = progress.percent

        progressCallback({ kind, title, description, value })
      }
    )

    // Initial progress
    progressCallback({ kind, title, value: 0 })
  }

  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
}
```

**File:** app/src/lib/remote-parsing.ts (L54-64)
```typescript
/** Parse the remote information from URL. */
export function parseRemote(url: string): IGitRemoteURL | null {
  for (const { protocol, regex } of remoteRegexes) {
    const match = regex.exec(url)
    if (match !== null && match.length >= 4) {
      return { protocol, hostname: match[1], owner: match[2], name: match[3] }
    }
  }

  return null
}
```

**File:** app/src/ui/clone-repository/clone-repository.tsx (L648-684)
```typescript
  private updateUrl = async (url: string) => {
    const parsed = parseRepositoryIdentifier(url)
    const tabState = this.getSelectedTabState()
    const lastParsedIdentifier = tabState.lastParsedIdentifier

    // If there is no path yet, just update the url
    if (tabState.path === null) {
      this.setSelectedTabState({ url }, this.validatePath)
      return
    }

    const safeName = parsed ? sanitizeCloneName(parsed.name) : null

    let newPath: string

    const dirPath = tabState.path
    if (lastParsedIdentifier) {
      if (safeName) {
        newPath = Path.join(Path.dirname(dirPath), safeName)
      } else {
        newPath = Path.dirname(dirPath)
      }
    } else if (safeName) {
      newPath = Path.join(dirPath, safeName)
    } else {
      newPath = dirPath
    }

    this.setSelectedTabState(
      {
        url,
        lastParsedIdentifier: parsed,
        path: newPath,
      },
      this.validatePath
    )
  }
```
