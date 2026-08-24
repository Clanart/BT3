I found a very strong analog. Let me verify the exact context around this environment variable in `clone.ts`.### Title
`clone()` explicitly disables Git's `GIT_CLONE_PROTECTION_ACTIVE` symlink/dotgit safety check, re-exposing recursive-submodule clone RCE - (File: `app/src/lib/git/clone.ts`)

### Summary
Git's `git clone` shells out with `GIT_CLONE_PROTECTION_ACTIVE` forced to `'false'`, unconditionally disabling the clone-time protections Git upstream added (in response to CVE-2024-32002) to stop a cloned repository's own submodule/`.git` layout from writing and then executing files outside the intended working tree. Desktop performs `--recursive` clones of attacker-supplied URLs and does not compensate for the disabled check anywhere else in the clone path.

### Finding Description
`clone()` builds the execution environment for `git clone` as: [1](#0-0) 

`GIT_CLONE_PROTECTION_ACTIVE` is a real upstream Git environment variable, added specifically to guard `git clone --recursive` against symlinked/case-collision `.git` paths inside nested submodules — the exact mechanism behind CVE-2024-32002, where a maliciously crafted repository with nested submodules can cause Git to write into (and, on Windows/macOS with case-insensitive filesystems, overwrite) files inside the `.git` directory instead of the working tree, ultimately leading to hook execution and arbitrary code execution during clone. Git ships this protection *enabled by default*; Desktop's clone path takes the extra step of setting it to `'false'`, i.e. actively turning the mitigation off, rather than merely omitting it.

The command itself confirms the attack surface is live: Desktop always clones with `--recursive`: [2](#0-1) [3](#0-2) 

This is the strongest match to the report's broken invariant: in the InvoiceManager case, a security-relevant check (`_isSolverActive`) exists in the code but is not applied where it matters, breaking an invariant the rest of the system relies on. Here, an analogous safety check (Git's own clone-time protection) exists upstream and is explicitly toggled off in the exact function that clones untrusted, attacker-controlled remote content, breaking the invariant that "cloning any URL a user pastes/clicks cannot write or execute outside the destination working tree."

The other guard present in the function, `isClonePathSensitive()`, only validates the *destination* path chosen by the user before the clone starts: [4](#0-3) 

It does nothing to prevent the *cloned repository's own content* (nested submodules, crafted `.gitmodules`, symlinked `.git` entries) from escaping the destination tree once the clone is running — which is precisely what `GIT_CLONE_PROTECTION_ACTIVE` is meant to stop, and precisely what is disabled here.

### Impact Explanation
If a user clones or opens a "Clone repository" URL (including via a `x-github-client://` deep link or an "Open in Desktop" button pointing at an attacker-controlled repository) containing a maliciously crafted nested submodule structure, Desktop's `--recursive` clone runs with the upstream anti-exploit check turned off. Depending on the underlying Git version and filesystem (case-insensitive HFS+/APFS/NTFS are the documented at-risk configurations for CVE-2024-32002), this can result in files being written into the repository's `.git` directory (e.g., hooks, config) and subsequently executed, i.e. code execution triggered purely by cloning a hostile repository — no local access, admin rights, or social engineering beyond "paste a repo URL / click a clone link" is required.

### Likelihood Explanation
The attacker only needs to control the content of a Git repository (fully within an unprivileged attacker's ability) and get a victim to clone it through Desktop's normal clone flow or a supported deep link. Desktop unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` on every clone with no feature flag or fallback re-enabling it, so the exposure is deterministic whenever this code path executes.

### Recommendation
Remove the explicit `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override so Git's default (enabled) clone-time protections apply. If the override exists to work around a specific compatibility issue, gate it narrowly (only for verified-safe/local clone sources) and otherwise rely on the bundled Git version's built-in protections; ensure the bundled `dugite`/Git version used by Desktop is at or above the version that ships the CVE-2024-32002 fix. Add a regression test asserting that clone execution options never disable this variable for remote URLs.

### Proof of Concept
1. Attacker publishes a GitHub repository containing a `.gitmodules` file that references a nested submodule structured to trigger the CVE-2024-32002 condition (nested submodule paths designed to collide with `.git`/`.GIT` on a case-insensitive filesystem, or symlink-based path traversal within the submodule tree).
2. Victim clones the repository through GitHub Desktop's "Clone repository" dialog or an "Open in Desktop" deep link pointing at the URL.
3. Desktop invokes `clone()` in `app/src/lib/git/clone.ts`, which runs `git -c init.defaultBranch=... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` in the environment (`app/src/lib/git/clone.ts:81-84`), disabling Git's own defense against the crafted submodule layout.
4. During the recursive submodule checkout, Git writes attacker-controlled content into the `.git` directory rather than the working tree and/or executes it, achieving code execution on the victim's machine as a direct result of cloning untrusted repository content.

Note: I was unable to execute the bundled Git/dugite version check in this environment to confirm whether the underlying Git binary shipped with this build is already patched against CVE-2024-32002 independent of the environment variable; verifying that version, and testing this PoC end-to-end, requires a live Desktop session with the bundled Git binary.

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

**File:** app/src/lib/git/clone.ts (L81-84)
```typescript
  const env = {
    ...(await envForRemoteOperation(url)),
    GIT_CLONE_PROTECTION_ACTIVE: 'false',
  }
```

**File:** app/src/lib/git/clone.ts (L88-93)
```typescript
  const args = [
    '-c',
    `init.defaultBranch=${defaultBranch}`,
    'clone',
    '--recursive',
  ]
```

**File:** app/src/lib/git/clone.ts (L119-125)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
```
