## Title
Clone operation explicitly disables Git's CVE-2024-32002 submodule symlink protection, enabling arbitrary code execution from a malicious repository - (File: `app/src/lib/git/clone.ts`)

### Summary
`clone()` unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the environment for every `git clone --recursive` invocation. [1](#0-0)  This environment variable is Git's own kill-switch for the fix that was shipped for CVE-2024-32002 (and the related submodule/symlink hook-write family of bugs), where a malicious repository with a crafted nested submodule can use a symlink in place of a `.git` directory entry to cause the recursive-clone/checkout machinery to write attacker-controlled files (including executable hooks) outside the intended submodule worktree — including into the top-level `.git/hooks` directory. Git's fix refuses such operations unless explicitly told the protection is inactive. By forcing this flag to `false`, Desktop is telling the embedded Git binary to behave as if it does not need to apply that protection, on every single clone the user performs, including clones of arbitrary/untrusted URLs entered by the user or reached via `x-github-client://openRepo` deep links (`openRepositoryFromUrl` → `openOrCloneRepository` → `clone`). [2](#0-1) 

### Finding Description
`clone()` builds the Git invocation with `--recursive` and injects `GIT_CLONE_PROTECTION_ACTIVE: 'false'` into the child process environment: [1](#0-0) 

There is no accompanying justification comment, no feature flag, and no path/ownership check that mitigates the disabled protection — the function's only defensive check, `isClonePathSensitive`, only prevents the *destination directory itself* from being a sensitive system path; it does nothing to constrain what a malicious remote's nested submodules can write once cloning proceeds. [3](#0-2) 

Contrast this with Desktop's existing "unsafe repository" trust model, which only fires when Git detects *dubious ownership* of an already-existing directory (`fatal: detected dubious ownership...`) via `getRepositoryType`: [4](#0-3)  A freshly cloned repository is owned by the current user, so this guard never triggers for content written during the clone itself — it is designed for a completely different threat model (locally pre-existing directories owned by another OS user), not for content an attacker smuggles in via a malicious remote during `--recursive` clone.

The broken invariant, mapped from the Sherlock report's pattern ("an attacker-controlled callback fires during an operation the victim initiated, subverting an assumed-safe code path"): here the "callback" is Git's own submodule/hook machinery invoked implicitly by `--recursive`, and the "receiver" is the malicious repository's crafted submodule tree. Git upstream added `GIT_CLONE_PROTECTION_ACTIVE` specifically so that this implicit, attacker-reachable code path cannot write outside the intended worktree; Desktop deliberately turns that check off for every clone.

### Impact Explanation
If the disabled protection is in fact exploitable against Desktop's bundled Git version (this could not be fully confirmed from local code alone — see Likelihood/uncertainty below), the impact is severe: a user who clones an attacker-controlled repository (public repo, malicious fork, or URL from a phishing link/deep link) could have arbitrary files written outside the submodule directory — most critically, executable Git hooks (`post-checkout`, `pre-commit`, etc.) — placed into the parent repository's `.git/hooks`. Those hooks would then be executed automatically by Git during the same clone/checkout or a subsequent operation, resulting in arbitrary code execution on the victim's machine under the Desktop process's privileges. This satisfies the "attacker controls a cloned/fetched repository ... result is code execution" criterion.

### Likelihood Explanation
Likelihood depends entirely on whether the specific dugite/embedded Git version bundled with Desktop is vulnerable to the underlying symlink/submodule write issue when the protection is disabled — Desktop ships its own Git binary via `dugite`/`resolveGitBinary`, and if that binary is patched, is not affected, or the flag is a no-op in the shipped version, there may be no live path (this repo snapshot does not let me inspect the bundled Git/dugite version or its source to confirm). What is clear and verifiable from local code is that Desktop explicitly requests the vulnerable/legacy behavior on every clone with no compensating control, no comment explaining why, and no restriction to trusted sources — that combination (explicit protection downgrade + attacker-supplied clone source + `--recursive`) is itself the concerning pattern, independent of confirming the exact CVE reachability in the shipped Git version.

### Recommendation
Remove `GIT_CLONE_PROTECTION_ACTIVE: 'false'` from `clone()` unless there is a narrowly-scoped, documented, and tested reason it is required (e.g., a specific submodule workflow that legitimately needs it); if such a reason exists, gate it behind an explicit, narrow condition rather than applying it unconditionally to every clone of arbitrary user-supplied URLs. At minimum, add inline documentation explaining why Git's CVE-2024-32002 mitigation is disabled, and add regression tests (mirroring `app/test/unit/git/clone-test.ts`) that assert a malicious submodule/symlink payload cannot cause writes outside the destination path during `--recursive` clone.

### Proof of Concept
Local-code evidence only (full end-to-end exploitation was not verified against the bundled Git binary):
1. `clone()` is reachable from user input in multiple ways: direct URL entry in the Clone dialog (`app/src/ui/clone-repository/clone-repository.tsx:763-796`) [5](#0-4) , and from `x-github-client://openRepo` deep links via `dispatcher.openRepositoryFromUrl` → `openOrCloneRepository`. [2](#0-1) 
2. Every such call ends up in `clone()`, which always passes `--recursive` and always sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'`, with no per-source distinction between trusted and untrusted URLs. [6](#0-5) 
3. An attacker crafts a repository containing a submodule entry whose corresponding `.git` file/symlink structure is designed to escape the submodule checkout path (the class of payload addressed by CVE-2024-32002). With the protection explicitly disabled, the recursive clone/checkout proceeds without Git's refusal check, allowing the payload to place a hook script outside the intended submodule directory (e.g., in the top-level `.git/hooks`), which subsequently executes.

I could not directly execute this end-to-end against the bundled `dugite` Git binary in this environment, so I cannot confirm the exact patch level of the shipped Git or whether the flag has any effect on it; this should be validated by a Devin session with terminal/filesystem access to build a malicious submodule fixture and run it through `clone()` with the bundled binary.

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

**File:** app/src/lib/git/clone.ts (L81-93)
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

**File:** app/src/lib/git/rev-parse.ts (L18-65)
```typescript
export async function getRepositoryType(path: string): Promise<RepositoryType> {
  if (!(await directoryExists(path))) {
    return { kind: 'missing' }
  }

  try {
    const result = await git(
      ['rev-parse', '--is-bare-repository', '--show-cdup', '--git-dir'],
      path,
      'getRepositoryType',
      { successExitCodes: new Set([0, 128]) }
    )

    if (result.exitCode === 0) {
      // Bare repositories will not include gitdir so we handle that separately
      if (result.stdout.startsWith('true\n')) {
        return { kind: 'bare' }
      }

      // --is-bare-repository and --show-cdup each produce a single line but
      // --git-dir could theoretically contain newlines so we parse the known
      // fields first and treat the remainder as the git dir. We use [\s\S]*
      // instead of .* for the git dir capture group because .* doesn't match
      // newlines whereas [\s\S]* matches any character including newlines.
      const match = result.stdout.match(/^(true|false)\n(.*)\n([\s\S]*)\n$/)

      if (match) {
        const [, isBare, cdup, gitDir] = match

        return isBare === 'true'
          ? { kind: 'bare' }
          : {
              kind: 'regular',
              topLevelWorkingDirectory: resolve(path, cdup),
              gitDir: resolve(path, gitDir),
            }
      }
    }

    const unsafeMatch =
      /fatal: detected dubious ownership in repository at '(.+)'/.exec(
        result.stderr
      )
    if (unsafeMatch) {
      return { kind: 'unsafe', path: unsafeMatch[1] }
    }

    return { kind: 'missing' }
```

**File:** app/src/ui/clone-repository/clone-repository.tsx (L763-796)
```typescript
  private clone = async () => {
    this.setState({ loading: true })

    const cloneInfo = await this.resolveCloneInfo()
    const { path } = this.getSelectedTabState()

    if (path == null) {
      const error = new Error(`Directory could not be created at this path.`)
      this.setState({ loading: false })
      this.setSelectedTabState({ error })
      return
    }

    if (!cloneInfo) {
      const error = new Error(
        `We couldn't find that repository. Check that you are logged in, the network is accessible, and the URL or repository alias are spelled correctly.`
      )
      this.setState({ loading: false })
      this.setSelectedTabState({ error })
      return
    }

    const { url, defaultBranch } = cloneInfo

    this.props.dispatcher.closeFoldout(FoldoutType.Repository)
    try {
      this.cloneImpl(url.trim(), path, defaultBranch)
    } catch (e) {
      log.error(`CloneRepository: clone failed to complete to ${path}`, e)
      this.setState({ loading: false })
      this.setSelectedTabState({ error: e })
    }
  }

```
