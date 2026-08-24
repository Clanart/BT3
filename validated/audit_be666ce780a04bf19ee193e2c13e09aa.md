Based on the investigation, I found a strong analog: GitHub Desktop's `clone()` function explicitly disables an upstream Git security control that exists specifically to stop malicious repository content from achieving code execution during clone.

### Title
Clone operation explicitly disables Git's `GIT_CLONE_PROTECTION_ACTIVE` safeguard, re-opening hook/symlink based code execution from a malicious clone URL - (File: `app/src/lib/git/clone.ts`)

### Summary
`clone()` builds the environment for every `git clone --recursive` invocation and unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` [1](#0-0) . This variable is Git's own opt-out for the clone-time protections against attacker-controlled repository content (e.g. crafted `.git/hooks` symlinks / nested-repository tricks that were the subject of Git's `CVE-2024-32004`-class fixes) being executed as part of the clone/checkout of a submodule or embedded repository. Desktop turns this protection off for every clone it performs, including clones of arbitrary attacker-supplied URLs entered by the user (File ▸ Clone repository, "Open in Desktop" deep links, drag-and-drop of a repo URL, etc.).

### Finding Description
The broken invariant is: "Git's clone-time hook/symlink protection must stay enabled when cloning content from an untrusted, attacker-influenced source." Desktop's `clone()` sets the environment for the `git clone --recursive` call as: [2](#0-1) 
`GIT_CLONE_PROTECTION_ACTIVE=false` is Git's internal kill-switch used by its own protections that were introduced to close exactly the class of "malicious repository being cloned locally/via submodules leads to code execution" bugs. By forcing this to `'false'`, Desktop disables that guard for every clone operation, including clones with `--recursive` (submodules), which is precisely the scenario the guard targets, since a submodule's repository content is attacker-controlled (it comes from whatever remote URL is configured in the parent repo's `.gitmodules`, which the attacker who controls the top-level repository also controls).

The only other guard present is `isClonePathSensitive()`, which only validates the destination directory (blocking clones into `~/.ssh`, `~/.gnupg`, etc.) [3](#0-2) . It does nothing to constrain what a submodule/nested repository can do once its own hooks directory or symlinked `.git` is materialized during the recursive clone — that is exactly the surface `GIT_CLONE_PROTECTION_ACTIVE` is meant to defend, and Desktop has explicitly turned it off.

### Impact Explanation
If a user clones (or "Open in Desktop"-deep-links into cloning) a malicious repository that has been crafted with a submodule pointing at another attacker-controlled repository, the recursive checkout of that submodule can, with Git's built-in protections disabled, result in execution of attacker-supplied hook code as part of the clone/checkout — i.e. arbitrary code execution on the victim's machine driven entirely by content in a repository the attacker controls, satisfying the "attacker controls a cloned/fetched repository" impact category (code execution outside the repo).

### Likelihood Explanation
Likelihood is high for any user who clones an untrusted repository through Desktop's normal clone flow (URL entry, "Clone repository" dialog, or `x-github-client://openRepo` style deep links), since `--recursive` submodule cloning is unconditionally requested [4](#0-3)  and the protection is disabled for every single clone, with no code path that re-enables it.

### Recommendation
Remove the `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override entirely and let Git's own clone protections run for every clone. If a legitimate reason required disabling it (e.g. a compatibility issue with a particular Git version or workflow), that should be scoped narrowly and documented with a comment explaining why it is safe, rather than applied unconditionally to all clone operations including `--recursive` submodule clones of arbitrary user-supplied URLs.

### Proof of Concept
1. Attacker publishes `evil.git` containing a `.gitmodules` entry pointing to `evil-sub.git`, itself crafted with the historical `CVE-2024-32004`-style layout (e.g. hooks directory materialized via clone content/symlink trickery) that Git's `GIT_CLONE_PROTECTION_ACTIVE` guard was built to block.
2. Victim clones `evil.git` via GitHub Desktop's Clone dialog (or a crafted "Open in Desktop" URL).
3. Desktop invokes `git -c init.defaultBranch=... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` in the environment [5](#0-4) .
4. Because the protection is disabled, the recursive submodule clone/checkout can trigger execution of attacker-supplied hook content, achieving code execution on the victim's system without any further, unnatural user action.

**Note on evidence limits:** I could not find any comment, test, or changelog entry in this repository explaining why `GIT_CLONE_PROTECTION_ACTIVE` is forced to `false` — the index doesn't contain a rationale, and I did not find the original PR that introduced this line. If there is a valid, narrowly-scoped reason for disabling this (e.g. a workaround verified upstream), that context isn't present in the codebase as indexed; a Devin session with full repo/history access could confirm via `git blame`/PR history.

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
