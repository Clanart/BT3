Confirmed: there is no Preferences UI or any other surface in the codebase to view or remove entries from the global `safe.directory` list once added — the only write path is `addSafeDirectory`/`addGlobalConfigValueIfMissing` in [1](#0-0)  and it is strictly additive.

### Title
Unbounded, unattested global `safe.directory` trust grant driven by attacker-influenced Git stderr path - (File: `app/src/lib/git/rev-parse.ts`, `app/src/lib/git/config.ts`)

### Summary
When Desktop encounters a Git "dubious ownership" error, it extracts a filesystem path directly out of Git's stderr text and later writes that exact path into the user's **global** `~/.gitconfig` `safe.directory` list when the user clicks "Trust Repository," with no verification that the path matches the repository the user actually intended to trust, and no way to undo the grant afterward from within the app.

### Finding Description
`getRepositoryType` parses the "unsafe" case purely from Git's stderr text: [2](#0-1) 
The captured `unsafeMatch[1]` is *whatever path Git reports*, not necessarily the top-level path the user is looking at in the "Add Existing Repository" or "Missing Repository" screens. Git derives this path by walking up to the actual `.git` directory that triggered the ownership check — which, via gitlinks (`.git` files with `gitdir: <path>`), submodules, or worktree admin files, can point to a completely different location than the folder the user selected, including a nested subfolder or, on some layouts, a path outside the repository tree.

The UI components blindly forward whatever path was returned to `addSafeDirectory`: [3](#0-2) [4](#0-3) 

`addSafeDirectory` then permanently appends that path to the **global** Git configuration (affecting every repository and every Git-invoking tool on the machine, not just the one folder shown in the dialog): [5](#0-4) [6](#0-5) 

The broken invariant mirrors the Sherlock report's pattern exactly: a security-relevant piece of state (`raiseTargetPercentage` there, the `safe.directory` allowlist here) is **set once, persists indefinitely, and the only mutator is one-directional (add-only / raise-only) with no corresponding "unset"/"remove" operation** anywhere in the codebase. There is no `removeSafeDirectory` function, no Preferences panel entry to review or revoke trusted directories, and no confirmation dialog that shows the user the literal path string about to be written to config — the dialog copy says "add an exception for this directory" but the value committed to disk is the Git-reported path, which the user never sees or confirms verbatim.

### Impact Explanation
Trusting an attacker-crafted cloned/fetched repository (e.g., extracted from an archive, checked out on a shared filesystem, or containing crafted gitlinks/submodules) can cause the global `safe.directory` allowlist to be widened to a path the user did not knowingly approve. Because `safe.directory` disables Git's ownership-mismatch protection — the exact protection meant to stop automatic execution of hooks/config from directories owned by another user — a mistrusted grant silently reopens the door to hook/config-based code execution for that path across all future Git operations on the machine (not just within Desktop), and the grant can never be revoked from the UI, only by manually editing `~/.gitconfig` outside the app.

### Likelihood Explanation
Any user who clones or opens an attacker-supplied repository containing nested `.git` links with mismatched ownership (a realistic scenario on shared drives, Docker-mounted volumes, or files extracted by a different user/process) will see the "unsafe" warning and, following the intended UX, click "Trust Repository" — a single, unmodified, expected user action. No admin rights, local access, or unnatural steps are required; the only precondition is that the user does what the dialog explicitly asks them to do, trusting the displayed directory, while the actual value persisted comes from unvalidated Git stderr text.

### Recommendation
1. Validate that the path returned in the `unsafe` `RepositoryType` result is exactly equal to (or a verified ancestor of) the path the user selected/is viewing before calling `addSafeDirectory`; refuse or explicitly flag a mismatch to the user.
2. Display the literal path that will be added to `safe.directory` in the confirmation UI so users can review it before trusting.
3. Add a "remove"/"untrust" capability (e.g., a Preferences section listing trusted directories with a delete action) so a mistaken or malicious grant can be reversed from within Desktop, analogous to adding a `setRaiseTargetPercentage(0)` path in the original report.

### Proof of Concept
1. Attacker builds a repository where the top-level `.git` is a gitlink file (`.git` → `gitdir: <crafted-path>`), and `<crafted-path>` is a directory owned by a different user/UID (e.g., set up on a shared multi-user filesystem or container mount).
2. Victim clones/extracts this repository and opens it in GitHub Desktop; `getRepositoryType` invokes `git rev-parse` at the top-level path, which resolves through the gitlink to `<crafted-path>`, fails ownership check, and Git prints `fatal: detected dubious ownership in repository at '<crafted-path>'`.
3. Desktop's regex captures `<crafted-path>` (not the folder the victim opened) via `rev-parse.ts:57-63`.
4. The victim, seeing the expected "Trust Repository" prompt referencing their own project name, clicks "Trust Repository."
5. `addSafeDirectory(<crafted-path>)` is called and permanently adds `<crafted-path>` — a directory the victim never directly inspected — to the global `safe.directory` list, disabling ownership protections for that path machine-wide, with no in-app way to remove the entry afterward.

### Citations

**File:** app/src/lib/git/config.ts (L176-206)
```typescript
/**
 * Adds a path to the `safe.directories` configuration variable if it's not
 * already present. Adding a path to `safe.directory` will cause Git to ignore
 * if the path is owner by a different user than the current.
 */
export async function addSafeDirectory(path: string) {
  // UNC-paths on Windows need to be prefixed with `%(prefix)/`, see
  // https://github.com/git-for-windows/git/commit/e394a16023cbb62784e380f70ad8a833fb960d68
  if (__WIN32__ && path[0] === '/') {
    path = `%(prefix)/${path}`
  }

  await addGlobalConfigValueIfMissing('safe.directory', path)
}

/** Set the global config value by name. */
export async function addGlobalConfigValueIfMissing(
  name: string,
  value: string
): Promise<void> {
  const { stdout, exitCode } = await git(
    ['config', '--global', '-z', '--get-all', name, value],
    __dirname,
    'addGlobalConfigValue',
    { successExitCodes: new Set([0, 1]) }
  )

  if (exitCode === 1 || !stdout.split('\0').includes(value)) {
    await addGlobalConfigValue(name, value)
  }
}
```

**File:** app/src/lib/git/rev-parse.ts (L57-63)
```typescript
    const unsafeMatch =
      /fatal: detected dubious ownership in repository at '(.+)'/.exec(
        result.stderr
      )
    if (unsafeMatch) {
      return { kind: 'unsafe', path: unsafeMatch[1] }
    }
```

**File:** app/src/ui/add-repository/add-existing-repository.tsx (L69-77)
```typescript
  private onTrustDirectory = async () => {
    this.setState({ isTrustingRepository: true })
    const { repositoryUnsafePath, path } = this.state
    if (repositoryUnsafePath) {
      await addSafeDirectory(repositoryUnsafePath)
    }
    await this.validatePath(path)
    this.setState({ isTrustingRepository: false })
  }
```

**File:** app/src/ui/missing-repository.tsx (L35-50)
```typescript
  private onTrustDirectory = async () => {
    this.setState({ isTrustingPath: true })
    const { unsafePath } = this.state
    const { repository } = this.props

    if (unsafePath) {
      await addSafeDirectory(unsafePath)
      const type = await getRepositoryType(repository.path)

      this.setState({ isTrustingPath: false })

      if (type.kind !== 'unsafe') {
        this.checkAgain()
      }
    }
  }
```
