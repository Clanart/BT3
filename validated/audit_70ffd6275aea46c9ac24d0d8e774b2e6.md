### Title
`clone()` explicitly disables Git's clone-time hook/symlink protection via `GIT_CLONE_PROTECTION_ACTIVE=false` - (File: `app/src/lib/git/clone.ts`)

### Summary
`GIT_CLONE_PROTECTION_ACTIVE` is a Git-native safety switch (introduced by upstream Git as part of the hardening around malicious-repository clone/checkout attacks, e.g. CVE-2024-32004-class issues, where a crafted repository — especially one containing nested/symlinked `.git` directories or submodules with hook scripts — could get its hooks or config executed during `clone --recursive`/checkout). Desktop's `clone()` function explicitly forces this protection **off** for every clone it performs.

### Finding Description
`clone()` in `app/src/lib/git/clone.ts` builds the environment for the underlying `git clone --recursive` invocation as: [1](#0-0) 
This unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` before shelling out to `git clone --recursive -- <url> <path>`: [2](#0-1) 

The report's broken invariant — "an attacker-controlled object (short position / here: attacker-controlled repository content) is allowed to drive an internal computation/guard into a state its author didn't anticipate, and an existing protective check is bypassed or turned off" — maps directly: Git's own upstream protection against malicious clone content is a guard designed to stop exactly the class of attack where the remote (attacker-controlled) content triggers unwanted execution during the clone flow. Desktop turns that guard off for **every** clone, including clones/opens of attacker-supplied URLs reached via:
- the `x-github-client://openrepo/<url>` deep link handled by `parseAppURL` / `handleAppURL` in `app/src/main-process/main.ts`, which is user-clickable and requires no local access, [3](#0-2) [4](#0-3) 
- the "Clone repository" UI, which resolves clone URLs from GitHub API objects (`fetchRepositoryCloneInfo`) that could point at attacker-controlled hosts/forks, [5](#0-4) 
- or any `cli-clone` command-line invocation. [6](#0-5) 

Because `--recursive` is always passed to `git clone`, any submodule referenced by the attacker's repository is also fetched and checked out in the same operation, and with the native protection disabled, a crafted nested repository/submodule (e.g. one using a symlinked `.git` entry or a hostile pre/post-checkout hook path smuggled through the submodule's own tree) is not blocked by Git's own safeguard the way it would be by default.

The other guards present in this file — `isClonePathSensitive` (blocks a handful of known-sensitive destination directories) and `sanitizeCloneName`/`parseRepositoryIdentifier` (prevents path traversal in the derived folder name) — do not address this: they only constrain the **destination path** of the clone, not what the **cloned repository's own tree/submodules** are allowed to do once protections around hook/symlink handling are relaxed. [7](#0-6) [8](#0-7) 

### Impact Explanation
If `GIT_CLONE_PROTECTION_ACTIVE=false` disables Git's built-in defense against malicious repository/submodule content during clone, an attacker who controls the target repository (or a repo whose submodules they control) reached via a deep link, a forked/attacker repo surfaced through the GitHub API, or a raw clone URL, can potentially cause code execution or file writes on the victim's machine as a side effect of the "Clone repository" action alone — no commit review or additional user interaction beyond clicking "Clone" (or the deep link) is required.

### Likelihood Explanation
Likelihood is high relative to the report's own AMM bug: unlike the DeFi report which requires paying large fees repeatedly, here the attacker only needs to host one malicious repository and get the victim to clone it via a single click (deep link or Clone-repository dialog), which is a normal, expected Desktop workflow. This is a stronger and more directly reachable analog than a griefing/DoS bug — it is a persistent, unconditional downgrade of Git's own security control on every clone Desktop performs.

### Recommendation
Do not set `GIT_CLONE_PROTECTION_ACTIVE=false` for `git clone`. Leave Git's default (protection-active) behavior in place, or set it explicitly to `'true'`, especially since `--recursive` is used and clone URLs frequently originate from untrusted, attacker-influenced sources (deep links, forked GitHub API repository objects, raw user input). If disabling it was done to work around a compatibility issue with a specific Git feature, gate that opt-out narrowly and only for trusted, first-party clone sources, not universally.

### Proof of Concept
1. Attacker publishes a repository (or a fork reachable through `IAPIRepository.clone_url`) containing a submodule entry (`.gitmodules`) pointing at a nested repository crafted to exploit the class of hook/symlink issue that `GIT_CLONE_PROTECTION_ACTIVE` is designed to block.
2. Attacker sends the victim a link such as `x-github-client://openrepo/<attacker-repo-url>` or shares the repository so it surfaces in Desktop's "Clone a repository" dialog. [3](#0-2) 
3. Victim clicks the link / clicks "Clone" in Desktop; Desktop calls `clone(url, path, options)`. [9](#0-8) 
4. `git clone --recursive -- <url> <path>` executes with `GIT_CLONE_PROTECTION_ACTIVE=false` in its environment, disabling the upstream Git safeguard for the whole operation (including the recursive submodule checkout). [10](#0-9) 
5. The attacker's crafted submodule/tree content is processed without the protection that Git itself would normally apply, potentially resulting in unwanted execution or writes outside the intended clone directory.

**Note on verification limits:** I could not find in this index any comment, test, or commit message explaining *why* `GIT_CLONE_PROTECTION_ACTIVE` is forced to `'false'` here (the only commit on this file in the indexed history is a single "Initial commit"), so I cannot confirm whether this is an intentional, justified trade-off (e.g., compatibility workaround) or an oversight. If you need the full history/rationale or want to confirm the exact upstream Git semantics of this variable, a Devin session with full repo/history and network access would be needed to check further.

### Citations

**File:** app/src/lib/git/clone.ts (L16-47)
```typescript
function isClonePathSensitive(unresolvedClonePath: string): boolean {
  const clonePath = Path.resolve(unresolvedClonePath).toLowerCase()
  const home = Path.resolve(homedir()).toLowerCase()

  if (clonePath === home) {
    return true
  }

  const sensitiveLocations = [
    Path.join(home, '.ssh'),
    Path.join(home, '.gnupg'),
    Path.join(home, '.config'),
    Path.join(home, '.config', 'git'),
    Path.join(home, '.gitconfig'),
  ]

  if (__WIN32__) {
    const appData = process.env.APPDATA
    if (appData) {
      sensitiveLocations.push(appData.toLowerCase())
      sensitiveLocations.push(Path.join(appData, 'gnupg').toLowerCase())
    }
  }

  for (const sensitive of sensitiveLocations) {
    if (clonePath === sensitive || clonePath.startsWith(sensitive + Path.sep)) {
      return true
    }
  }

  return false
}
```

**File:** app/src/lib/git/clone.ts (L68-80)
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

```

**File:** app/src/lib/git/clone.ts (L81-125)
```typescript
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

**File:** app/src/main-process/main.ts (L282-291)
```typescript
  if (typeof args['cli-open'] === 'string') {
    handleCLIAction({ kind: 'open-repository', path: args['cli-open'] })
  } else if (typeof args['cli-clone'] === 'string') {
    handleCLIAction({
      kind: 'clone-url',
      url: args['cli-clone'],
      branch:
        typeof args['cli-branch'] === 'string' ? args['cli-branch'] : undefined,
    })
  }
```

**File:** app/src/ui/clone-repository/clone-repository.tsx (L739-750)
```typescript
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
