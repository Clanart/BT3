### Title
`git clone` explicitly disables Git's `GIT_CLONE_PROTECTION_ACTIVE` symlink/hook guard for attacker-controlled remotes, enabling hook execution via a malicious repository - (File: `app/src/lib/git/clone.ts`)

### Summary
GitHub Desktop's `clone()` function unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` when invoking `git clone --recursive` against an arbitrary, user- or link-supplied remote URL. This is the exact same class of bug as the Centrifuge finding: a protective mode that Git enables by default to stop untrusted/attacker-controlled input from triggering unsafe execution is deliberately turned off for the very operation where the untrusted content (the remote repository, potentially reached via a clone URL from a deep link or API object) is processed.

### Finding Description
`GIT_CLONE_PROTECTION_ACTIVE` is the environment variable Git introduced as part of its fix for the recursive-clone submodule hook execution vulnerability (the family of issues fixed as CVE-2024-32004/CVE-2024-32465): when cloning a repository recursively, a maliciously crafted repository/submodule structure (symlinked `.git`, embedded `hooks` directories, case-folding/NTFS tricks, etc.) could cause Git to execute a hook file that came from the untrusted remote during the clone/checkout, rather than being pure data. Git's fix makes this guard active (protection ON) by default; the variable exists primarily so that this guard can be *disabled* in trusted, controlled test contexts.

In this codebase, `clone()` in `app/src/lib/git/clone.ts` builds the execution environment as: [1](#0-0) 
and then runs `git -c init.defaultBranch=... clone --recursive -- <url> <path>` with that environment: [2](#0-1) 

`url` here is attacker-influenceable: it is the same clone URL that flows in from the "Clone repository" dialog, from a `github-mac://openRepo/<url>` / `x-github-client://` deep link, or from a GitHub API repository object surfaced through the app (`openRepositoryFromUrl` → `openOrCloneRepository` → eventually `clone()`), as shown by the deep-link parsing and dispatch code: [3](#0-2) [4](#0-3) 

The `--recursive` flag means submodules are fetched and checked out as part of the same clone invocation — this is exactly the scenario the upstream Git protection targets (malicious submodule trees whose hook paths get materialized on checkout). By forcing `GIT_CLONE_PROTECTION_ACTIVE=false`, Desktop removes Git's own safety net at precisely the moment it processes fully untrusted, remote-supplied repository content, mirroring the Centrifuge bug where `unpaidMode` (a trust-relaxing mode meant for legitimate paths) stayed enabled while executing attacker-supplied (`UntrustedContractUpdate`) content — the guard that should gate untrusted execution is turned off/left off exactly where the untrusted payload is processed.

Note: `withHooksEnv`/`interceptHooks` gating (`app/src/lib/git/core.ts`, `app/src/lib/hooks/with-hooks-env.ts`) is a *separate*, opt-in mechanism for intercepting the local repository's own configured hooks for UI features (commit hooks, etc.) and is not passed for `clone()`, so it does not mitigate this — the disabled protection is Git's own built-in clone-time defense, not Desktop's hooks interception feature.

### Impact Explanation
If the `GIT_CLONE_PROTECTION_ACTIVE` guard is what stands between a crafted, symlink/case-folding-abusing repository (reached by cloning any attacker-controlled URL — including one delivered via a deep link, "Clone with GitHub Desktop" web button, or GitHub API repository metadata surfaced in the app) and hook/script execution during checkout, disabling it removes Desktop's last line of defense against this specific hook-execution class of attack for recursive clones. Successful exploitation would result in arbitrary code execution on the user's machine at clone time, under the privileges of the Desktop process — a severe, unprompted (the user only clicks "Clone") remote-code-execution primitive, squarely matching the requested impact category ("attacker controls a cloned/fetched repository... code execution").

### Likelihood Explanation
Likelihood depends entirely on whether the bundled/embedded Git version's clone-time symlink/hook checks are otherwise sufficient without this flag, and on reproducing a concrete payload repository that Git's guard would have blocked. That verification requires running the actual embedded `dugite`/Git binary against a crafted malicious repository — something outside what static code inspection here can confirm. What is certain from the code is that Desktop actively and unconditionally opts out of a Git-provided anti-exploitation control for every clone of an arbitrary, attacker-suppliable URL, which is inherently suspicious and warrants a targeted PoC/regression test with the exact embedded Git version.

### Recommendation
- Remove the `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override in `app/src/lib/git/clone.ts` (or gate it strictly behind, e.g., an internal test-only code path/flag) so recursive clones of arbitrary remote URLs run with Git's default hook/symlink protections active.
- Add a regression test that clones a crafted repository (symlinked `.git`, colliding submodule/hooks paths) and asserts no hook script from the untrusted remote executes.
- Audit other callers of `git(...)` that pass `env` with security-relevant Git variables to ensure none of them silently disable upstream Git protections for remote/untrusted operations.

### Proof of Concept
Not independently reproducible from static analysis alone — validating requires: (1) confirming the exact embedded Git/dugite version's behavior for `GIT_CLONE_PROTECTION_ACTIVE`, and (2) constructing a malicious repository (e.g., with a submodule or symlink structure) that only fails to execute a hook when the protection is left active, then calling Desktop's `clone()` (or triggering it via `x-github-client://openRepo/<attacker-repo-url>`) and observing hook execution. This would need to be done in a live/dynamic environment (git binary + filesystem), which is outside the scope of this static code review; I flag this as the concrete mechanism found in code, with dynamic verification as the necessary next step.

### Citations

**File:** app/src/lib/git/clone.ts (L81-84)
```typescript
  const env = {
    ...(await envForRemoteOperation(url)),
    GIT_CLONE_PROTECTION_ACTIVE: 'false',
  }
```

**File:** app/src/lib/git/clone.ts (L88-125)
```typescript
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

**File:** app/test/unit/parse-app-url-test.ts (L26-51)
```typescript
  describe('openRepo via HTTPS', () => {
    it('returns right name', () => {
      const result = parseAppURL(
        'github-mac://openRepo/https://github.com/desktop/desktop'
      )
      assert.equal(result.name, 'open-repository-from-url')

      const openRepo = result as IOpenRepositoryFromURLAction
      assert.equal(openRepo.url, 'https://github.com/desktop/desktop')
    })

    it('returns unknown when no remote defined', () => {
      const result = parseAppURL('github-mac://openRepo/')
      assert.equal(result.name, 'unknown')
    })

    it('adds branch name if set', () => {
      const result = parseAppURL(
        'github-mac://openRepo/https://github.com/desktop/desktop?branch=cancel-2fa-flow'
      )
      assert.equal(result.name, 'open-repository-from-url')

      const openRepo = result as IOpenRepositoryFromURLAction
      assert.equal(openRepo.url, 'https://github.com/desktop/desktop')
      assert.equal(openRepo.branch, 'cancel-2fa-flow')
    })
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1955)
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

    if (repository === null) {
      return
    }
```
