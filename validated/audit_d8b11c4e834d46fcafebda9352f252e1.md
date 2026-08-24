## Title
Git "unsafe repository" trust dialog can silently whitelist an attacker‑controlled path instead of the repository the user actually reviewed - ([File: app/src/lib/git/rev-parse.ts])

### Summary
GitHub Desktop implements its own "trust this repository" governance layer on top of Git's built‑in ownership‑based protection (the fix for CVE‑2022‑24765). When a repository triggers Git's *dubious ownership* check, Desktop shows a "Trust Repository" dialog and, on confirmation, adds a path to the **global**, persistent `safe.directory` allow‑list. The path that gets trusted is not the path the user browsed to or is looking at — it is extracted verbatim from Git's stderr message with a regex, which can be made to name a different, attacker‑influenced location.

### Finding Description
`getRepositoryType` determines "unsafe" status by regex‑matching Git's stderr output and trusts whatever path Git echoes back as the object to remember: [1](#0-0) 

That value (`unsafeMatch[1]`) becomes `RepositoryType.unsafe.path` and is surfaced to the UI as `repositoryUnsafePath`/`unsafePath`, displayed to the user as "the Git repository ... appears to be owned by another user," with a "Trust Repository" / "add an exception" action: [2](#0-1) [3](#0-2) 

Clicking that action calls `addSafeDirectory(repositoryUnsafePath)`, which writes the value directly into the user's **global** `~/.gitconfig` via `git config --global --add safe.directory <path>`: [4](#0-3) 

The invariant Desktop *should* preserve is: "the path the user consciously reviews and consents to trust is the same path that gets whitelisted." That invariant is broken because Git's dubious-ownership message reports the path of the **resolved git directory** it actually operated on, not necessarily the directory the user opened. A repository can redirect Git elsewhere via a `.git` *file* (`gitdir: <path>`) — the standard mechanism used for worktrees and submodules — so a crafted/cloned repository can cause the fatal message (and thus the value Desktop persists to `safe.directory`) to reference an arbitrary path chosen by whoever built the repository, while the dialog UI still frames it as "trust this repository."

This mirrors the "hidden governance" bug class from the seed report: two authorization layers exist (Git's core ownership check and Desktop's own trust-granting UI), and the second layer does not faithfully bind its grant to the object the user believes they are authorizing — it blindly relays an attacker-influenceable string into a durable, system‑wide allow‑list.

### Impact Explanation
`safe.directory` is a machine‑wide, cross‑application exception list — it is honored by every invocation of `git` for that user, not just inside GitHub Desktop. Getting an unintended path added:
- Silently corrupts the user's trust configuration by writing to `~/.gitconfig` outside the repository the user thought they were vetting.
- Can be leveraged on shared/multi-user or predictable-path systems (e.g. temp/shared mounts) where the attacker can later populate the now‑whitelisted directory with a malicious `.git` config (`core.fsmonitor`, `core.hooksPath`, etc.) — exactly the class of local config/hook execution that `safe.directory` was introduced to block.
- The user's one-time "click to trust" consent is silently broadened to a path they never visually confirmed, since the dialog text quotes the resolved-but-attacker-influenced path.

### Likelihood Explanation
Requires the victim to open/clone an attacker-supplied repository (or a repository containing a crafted `.git` file/worktree redirection) in Desktop and to click through the existing "Trust Repository" warning — a normal, expected user flow for legitimately encountering unsafe-ownership repos (e.g. from network shares), which somewhat lowers suspicion since the warning already primes the user to expect an "owned by another user" message. No admin rights, local access, or credential compromise is needed beyond the standard "user opens a cloned repo" primitive that is explicitly in scope.

### Recommendation
- Do not trust the path embedded in Git's stderr message for governance decisions. Instead, resolve and display the actual `gitdir`/working directory Desktop itself resolved for the path the user selected (e.g., via `getRepositoryType`'s own `--show-cdup`/`--git-dir` resolution logic), and only ever add that canonical path to `safe.directory`.
- Bind the trust grant to the specific directory the user navigated to/selected, and reject or explicitly call out cases where Git's redirected gitdir differs from the top-level path the user is looking at.
- Consider scoping trust exceptions rather than writing unconditionally to the global config, and surfacing the exact resolved path to the user before persisting it.

### Proof of Concept
1. Attacker publishes a repository containing a top-level `.git` file (not a directory) with `gitdir: <attacker-chosen-path>` pointing at a path with different simulated ownership (or reachable via a shared/predictable location on the victim's machine).
2. Victim clones/opens this repository ("Add Local Repository" / opens a folder) in GitHub Desktop.
3. `getRepositoryType` runs `git rev-parse --is-bare-repository --show-cdup --git-dir` in the top-level folder; Git follows the `gitdir:` redirection, detects dubious ownership on the redirected path, and emits `fatal: detected dubious ownership in repository at '<attacker-chosen-path>'`.
4. `rev-parse.ts` (`app/src/lib/git/rev-parse.ts:57-63`) extracts `<attacker-chosen-path>` and returns it as `{ kind: 'unsafe', path: <attacker-chosen-path> }`, which is different from the folder the user actually opened.
5. Desktop's dialog shows this attacker-chosen path as "the Git repository appears to be owned by another user" and the user clicks "add an exception."
6. `onTrustDirectory` calls `addSafeDirectory(<attacker-chosen-path>)`, permanently adding that path to the global `safe.directory` list in `~/.gitconfig` — a path the user did not directly choose or fully verify, extending trust machine-wide beyond the repository they believed they were authorizing.

### Citations

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

**File:** app/src/lib/git/config.ts (L176-189)
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
```
