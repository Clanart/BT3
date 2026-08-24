### Title
`clone()` forces `GIT_CLONE_PROTECTION_ACTIVE=false`, disabling Git's CVE-2024-32002 recursive-clone hardening on every "Open in Desktop" / CLI clone - ([File: app/src/lib/git/clone.ts])

### Summary
`clone()` always sets the environment variable `GIT_CLONE_PROTECTION_ACTIVE` to the literal string `'false'` before invoking `git clone --recursive`, unconditionally overriding Git's own built-in protection that was added upstream specifically to stop malicious repositories from writing files outside the intended checkout during a recursive submodule clone. [1](#0-0) 

### Finding Description
The FiberRouter report is a case where a downstream consumer of untrusted external input (`bridge`/`crossToken` swap parameters) failed to preserve a security invariant that the underlying protocol expected to be enforced, letting an attacker-supplied value flow through unchecked and corrupt program state. The analogous broken invariant in GitHub Desktop is in the clone path: Desktop clones attacker-influenced repositories (via `x-github-client://openRepo/...` deep links, "Clone repository" URL fields, or `--cli-clone`), and by default runs `git clone --recursive`, which walks into every submodule the remote repository declares, including submodules pointing at nested/symlinked `.git` directories crafted by the attacker.

Git 2.45.1 introduced `GIT_CLONE_PROTECTION_ACTIVE` as a runtime hardening flag tied to the fix for CVE-2024-32002 (a case where a maliciously crafted repository with symlinked `.git` and submodule paths could get Git to write into the `.git` directory of the parent repo during a recursive clone/checkout on case-insensitive or symlink-tolerant filesystems, leading to hook execution / arbitrary write). Desktop's `clone()` explicitly sets this variable to `'false'`, which turns that protection off for every clone Desktop performs, rather than deferring to Git's own default (active) behavior. [1](#0-0) 

The attacker's control surface is exactly the type this task requires: a cloned/fetched repository the attacker crafts and offers via a link the user clicks (`open-repository-from-url` action reaching `openOrCloneRepository`) or a URL a user pastes into "Clone repository." [2](#0-1) [3](#0-2) 

Existing guards in this file — `isClonePathSensitive()` (blocks cloning *into* `~/.ssh`, `~/.gnupg`, etc.) and the `sanitizeCloneName`/`resolveWithin` checks used elsewhere for `filepath` — only constrain the *top-level destination directory* Desktop itself chooses. None of them inspect or restrict what the recursive clone/submodule-init step writes *inside* the working tree once cloning begins, which is precisely the surface Git's own `GIT_CLONE_PROTECTION_ACTIVE` guard targets. Forcing it to `'false'` therefore removes a defense-in-depth check without adding any Desktop-side replacement for the specific class of writes it prevents. [4](#0-3) 

### Impact Explanation
If the installed Git version relies on `GIT_CLONE_PROTECTION_ACTIVE` (rather than an unconditional fix) to prevent the CVE-2024-32002-class write-outside-worktree during recursive clone/submodule checkout, Desktop's forced `'false'` value re-opens that write primitive for any repository a user clones through Desktop's UI or accepts via an "Open in Desktop" deep link — potentially corrupting the local `.git` directory/hooks and leading to code execution on next Git invocation. This satisfies the required impact class: "code execution, file write ... outside the repo ... from an attacker-controlled cloned/fetched repository."

### Likelihood Explanation
Likelihood depends on the exact Git version bundled/required by Desktop and how it implements the fix (some Git versions removed the opt-out later, making the variable inert); this could not be fully confirmed from the indexed source alone since the vendored Git binary version and its exact protection semantics aren't visible in this repo's TypeScript sources. The trigger itself requires no unusual user action beyond the normal, expected flow of cloning/opening a repository via Desktop, which is a core designed feature, so likelihood is high assuming a vulnerable Git version is in use, and low/moot if Desktop bundles a Git build where the CVE fix is unconditional.

### Recommendation
Do not force-disable `GIT_CLONE_PROTECTION_ACTIVE`. Either omit the variable entirely (let Git's shipped default apply) or explicitly set it to `'true'`/leave unset, and verify the bundled `dugite`/Git version's behavior with this flag before considering any override at all.

### Proof of Concept
1. Bundle/install a Git version where `GIT_CLONE_PROTECTION_ACTIVE` gates the CVE-2024-32002 mitigation.
2. Host a malicious repository containing a submodule that references a symlinked/embedded `.git` path crafted to escape into the parent `.git` directory during recursive checkout.
3. Have a victim open `x-github-client://openRepo/https://attacker.example/evil/repo` (parsed by `parseAppURL` into an `open-repository-from-url` action) or paste the URL into Desktop's clone dialog. [2](#0-1) 
4. Desktop calls `clone()`, which runs `git clone --recursive` with `GIT_CLONE_PROTECTION_ACTIVE=false` in the environment, disabling the mitigating check. [5](#0-4) 
5. If the local Git binary depends on that flag, the submodule/checkout step writes outside the intended working tree as in CVE-2024-32002.

**Note on confidence:** This finding is based on identifying an unconditional override of a named upstream Git security-hardening environment variable in the clone path that handles fully attacker-controlled remote input. I could not verify from the indexed sources which exact Git/`dugite` version ships with this build of Desktop or whether that version's implementation of the CVE-2024-32002 fix is still gated by this variable — confirming exploitability end-to-end would require checking the vendored `dugite`/Git version and testing against it, which is outside what the code index can show. If you need the exact vendored Git version pinned in `package.json`/`dugite`, a full Devin session with repository access would be needed to confirm this precisely.

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

**File:** app/src/lib/git/clone.ts (L81-123)
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1951)
```typescript
  private async openRepositoryFromUrl(action: IOpenRepositoryFromURLAction) {
    const { url, pr, branch, filepath } = action

    let repository: Repository | null

    if (pr !== null) {
      repository = await this.openPullRequestFromUrl(url, pr)
    } else if (branch !== null) {
      repository = await this.openBranchNameFromUrl(url, branch)
    } else {
      repository = await this.openOrCloneRepository(url)
    }
```
