The Flatmoney bug is fundamentally about a mandatory safety check (Pyth price update) being silently disabled/bypassed via an attacker-influenced code path. There's a direct analog in this repository: GitHub Desktop unconditionally disables Git's own anti-RCE clone protection for every recursive clone.

### Title
Recursive clone unconditionally disables Git's submodule-hook RCE protection (`GIT_CLONE_PROTECTION_ACTIVE=false`) - (File: app/src/lib/git/clone.ts)

### Summary
`clone()` in `app/src/lib/git/clone.ts` always runs `git clone --recursive` with the environment variable `GIT_CLONE_PROTECTION_ACTIVE` hardcoded to `'false'`. This variable gates the fix Git shipped for CVE-2024-32002 (arbitrary hook execution via malicious/symlinked submodule `.git` directories during recursive clone). Desktop disables this check on every clone regardless of whether the remote is trusted, restoring the exact vulnerable behavior the upstream Git fix was meant to close.

### Finding Description
`clone()` builds its argument list with `--recursive` and merges in an environment object that always contains `GIT_CLONE_PROTECTION_ACTIVE: 'false'`: [1](#0-0) 

This is not conditioned on repository trust, remote host, or any user confirmation — it applies to every clone Desktop performs, including clones initiated from a user-typed URL, from a deep link action (`x-github-client://openRepo/...` parsed in `parseAppURL`), or from re-cloning a `gitHubRepository.cloneURL` recorded from the GitHub API in `MissingRepository.cloneAgain`: [2](#0-1) [3](#0-2) 

The corrupted invariant is Git's own protection (introduced upstream for CVE-2024-32002) which is designed to abort a recursive clone when a submodule's resolved `.git` directory would alias/escape via symlinks or case-insensitive filesystem tricks — a mechanism attackers use to plant a hook file (e.g. `post-checkout`, `pre-commit`) outside the intended `.git/modules/<name>` location, so that it later executes with the user's privileges when any git command runs against the parent (or a sibling) working tree. By forcing `GIT_CLONE_PROTECTION_ACTIVE=false`, Desktop tells Git to skip that abort check for the whole `--recursive` clone operation, i.e., it behaves exactly like the pre-fix, vulnerable Git.

Note that Desktop does have other defenses nearby — the "unsafe ownership" (`safe.directory`) trust prompt in `add-existing-repository.tsx`/`missing-repository.tsx`, and the `isClonePathSensitive` backstop against path-traversal into `.ssh`/`.gnupg`/`.gitconfig` — but neither of those guards inspects submodule content or symlink structure inside the cloned tree, so neither prevents the CVE-2024-32002 class of attack that `GIT_CLONE_PROTECTION_ACTIVE` was specifically built to stop: [4](#0-3) 

### Impact Explanation
An attacker who controls a repository/fork the victim clones through Desktop (via URL entry, "Clone repository" dialog, or an `openRepo` deep link) can craft a malicious submodule structure that, thanks to the disabled protection, is allowed to write an executable hook file outside its intended sandboxed submodule git-dir. That hook then executes automatically on subsequent Git operations Desktop performs in the repository (checkout, commit, etc.), yielding code execution with the victim's OS-user privileges — matching the "attacker controls a cloned/fetched repository ... result is code execution" category in scope.

### Likelihood Explanation
Every single clone operation in Desktop goes through this code path (`--recursive` is always passed and the override is unconditional), so exploitation only requires convincing a victim to clone or re-clone an attacker-authored/forked repository — a normal, expected Desktop workflow, not a contrived local-access or malware-preexisting scenario.

### Recommendation
Do not force `GIT_CLONE_PROTECTION_ACTIVE` to `'false'`. Only disable it (if ever necessary for compatibility) after confirming trust of the remote/content, and prefer leaving Git's default (protection-enabled) behavior intact for `--recursive` clones from arbitrary/untrusted URLs. At minimum, gate the override behind the same trust boundary already used for "unsafe repository" ownership warnings, or remove the override entirely and rely on Git's built-in protection.

### Proof of Concept
1. Attacker publishes a repository containing a submodule configured (via `.gitmodules`) whose module path, when cloned recursively, resolves through a symlink or case-collision to a location outside `.git/modules/<name>` (the pattern fixed by CVE-2024-32002), placing an executable file such as `hooks/post-checkout` where Git will later execute it.
2. Victim opens Desktop and clones the attacker's URL (via the Clone dialog, a pasted GitHub URL, or an `x-github-client://openRepo/<url>` deep link handled by `parseAppURL`/`dispatchURLAction`).
3. `clone()` runs `git -c init.defaultBranch=... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` in the environment, so Git's submodule-clone safety abort is skipped and the planted hook is written to disk. [5](#0-4) 
4. The next Git operation Desktop performs in that working tree (e.g., a subsequent commit, checkout, or even the implicit status refresh) triggers the planted hook, executing attacker-controlled code as the victim.

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

**File:** app/src/ui/missing-repository.tsx (L169-188)
```typescript
  private cloneAgain = async () => {
    const gitHubRepository = this.props.repository.gitHubRepository
    if (!gitHubRepository) {
      return
    }

    const cloneURL = gitHubRepository.cloneURL
    if (!cloneURL) {
      return
    }

    try {
      await this.props.dispatcher.cloneAgain(
        cloneURL,
        this.props.repository.path
      )
    } catch (error) {
      this.props.dispatcher.postError(error)
    }
  }
```
