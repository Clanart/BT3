### Title
TOCTOU symlink race in `x-github-client://openrepo` deep-link file-open flow allows read/write outside the cloned repository - (File: `app/src/ui/dispatcher/dispatcher.ts`)

### Summary
The `openrepo` deep-link handler resolves an attacker-supplied `filepath` against the repository root with `resolveWithin()` (the "check"), then later calls `shell.showItemInFolder()` on the resolved path (the "use"). `resolveWithin()` validates the path by calling `realpath()` on the *check-time* filesystem state but returns the **original, symlink-unresolved** path string rather than the resolved real path. Because the checked repository is one the attacker fully controls (it was just cloned from the attacker's URL), an attacker-triggered background process (e.g. a `--recursive` submodule or a hook-driven process) can swap a directory entry for a symlink pointing outside the repository in the gap between the check and the use, causing Desktop to open/reveal a file outside the intended repository root. This mirrors the `liquidity-swap` bug's broken invariant: a value (pool balance / path validity) is checked once, an external, attacker-influenced operation happens, and the stale check result is used to act.

### Finding Description
`Dispatcher.openRepositoryFromUrl` handles the `x-github-client://openrepo` protocol action, which is fully attacker-controlled (URL, branch, and `filepath` query parameters are all supplied via the deep link): [1](#0-0) 

The sequence is:
1. `openOrCloneRepository(url)` clones the attacker's repository (`git clone --recursive`) — an operation the attacker fully controls, including submodule content and any git hooks that may run during checkout. [2](#0-1) 
2. Desktop then validates the attacker-supplied `filepath` with `resolveWithin(repository.path, filepath)` — this is the **time of check**.
3. If the check passes, Desktop calls `shell.showItemInFolder(resolved)` — this is the **time of use**.

The check itself has a design flaw that makes the race exploitable: `_resolveWithin` computes `realpath()` of both the root and the candidate path to verify containment, but then returns the *non-realpath'd* `resolved` string: [3](#0-2) 

Because the returned value (`resolved`) is not the resolved-symlink path that was actually verified, any symlink component in the path is re-resolved by the OS again at time of use. If an attacker-controlled process inside the freshly cloned repository (e.g., a background job spawned during the recursive submodule clone, or timed filesystem writes) replaces a directory or file at that path with a symlink pointing to a sensitive location (`~/.ssh`, `~/.aws`, app config, etc.) *after* the `realpath` check but *before* `shell.showItemInFolder` executes, Desktop will reveal/open the attacker-chosen target instead of a file inside the repository. The existing guards — `isAbsolute(filepath)` rejection and `resolveWithin` — only validate the state at check time and do not re-validate at use time, so they do not stop this path.

### Impact Explanation
An attacker who gets a victim to click a crafted `x-github-client://openrepo?url=...&filepath=...` deep link (e.g. embedded in a webpage, README, or chat message) can, without any local access, privileges, or pre-existing malware on the host:
- Cause GitHub Desktop to reveal/open a file or folder outside the cloned repository root, in the victim's file explorer/Finder, potentially exposing SSH keys, cloud credentials, or other sensitive files (file read outside the repo).
- Depending on how the OS shell handles the revealed item, this can also be leveraged for further exploitation (e.g., surfacing an executable outside the trusted repo boundary for the user to run).

This falls under "attacker controls a cloned/fetched repository ... or a deep link the user clicks, and the result is ... file read outside the repo," matching the valid-impact criteria.

### Likelihood Explanation
Exploitation requires: (1) the victim to click a single deep link — a normal, expected Desktop feature, not an unnatural step; (2) the attacker's repository to contain content that can win a narrow timing race (e.g., a submodule or hook-triggered background process that swaps a symlink between the `resolveWithin` check and `shell.showItemInFolder` use). The race window is a single Node.js macrotask boundary, which is narrow but not infeasible to win reliably by delaying the filesystem change until clone/checkout activity settles and racing on repeated attempts, since the attacker fully controls the timing of their own malicious repository content. This is a plausible-but-not-trivial-to-reproduce TOCTOU, analogous in class to the report's swap-state race, and is likely lower severity in practice due to the very small window, but the design flaw (returning the unresolved path from `resolveWithin`) removes the intended defense-in-depth.

### Recommendation
- Change `_resolveWithin` in `app/src/lib/path.ts` to return the fully resolved (`realResolved`) path rather than the symlink-unresolved `resolved` path, so the value that was validated is the value that is actually used.
- In `openRepositoryFromUrl` (`app/src/ui/dispatcher/dispatcher.ts`), re-validate containment (e.g., re-run `realpath` and compare against the repository root) immediately before calling `shell.showItemInFolder`, or open a file descriptor/handle at check time and use that same handle for the "use" step (open-then-use pattern) instead of re-resolving a path string.
- Consider opening the resolved path with `O_NOFOLLOW`-style semantics where the platform API allows it, to prevent symlink substitution entirely.

### Proof of Concept
1. Attacker crafts a public repository containing a `.gitmodules` entry (or another mechanism triggered during `git clone --recursive`) that, once cloned, spawns a short-lived background task which replaces a known subpath (e.g. `subdir/link`) with a symlink pointing to `~/.ssh` shortly after checkout completes.
2. Attacker sends the victim a link:
   `x-github-client://openrepo/attacker/repo?filepath=subdir/link`
3. Victim clicks the link. Desktop clones the attacker's repository via `openOrCloneRepository`.
4. `openRepositoryFromUrl` calls `resolveWithin(repository.path, 'subdir/link')`; at this instant `subdir/link` is still a regular file inside the repo, so the `realpath` check passes and `resolved` (`<repoPath>/subdir/link`) is returned.
5. Before the subsequent `shell.showItemInFolder(resolved)` call executes, the attacker's background task (triggered from step 1) replaces `subdir/link` with a symlink to `~/.ssh`.
6. `shell.showItemInFolder` opens the OS file browser at `~/.ssh` instead of inside the repository, exposing the victim's SSH keys in the file explorer — outside the intended repository boundary, despite the `resolveWithin` guard having "passed."

Note: the exact reliability of winning the race (step 5 timing) could not be empirically verified from static code review alone; a background Devin agent with full repo/test access should build a timing harness against `resolveWithin`/`openRepositoryFromUrl` to confirm exploitability and hardening effectiveness.

### Citations

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1973)
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

    if (filepath !== null) {
      if (isAbsolute(filepath)) {
        log.error(`Refusing to open absolute path: ${filepath}`)
        return
      }

      const resolved = await resolveWithin(repository.path, filepath)

      if (resolved !== null) {
        shell.showItemInFolder(resolved)
      } else {
        log.error(
          `Prevented attempt to open path outside of the repository root: ${filepath}`
        )
      }
    }
  }
```

**File:** app/src/lib/git/clone.ts (L86-126)
```typescript
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

**File:** app/src/lib/path.ts (L64-72)
```typescript
  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
}
```
