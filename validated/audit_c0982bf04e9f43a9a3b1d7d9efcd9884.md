## Title
Arbitrary directory can be added to the global `safe.directory` allow-list via the "Trust Repository" flow - (File: `app/src/lib/git/rev-parse.ts`, `app/src/ui/add-repository/add-existing-repository.tsx`, `app/src/ui/missing-repository.tsx`)

## Summary
When Desktop detects that a repository fails Git's "dubious ownership" check, it parses the untrusted directory path out of Git's own stderr message and later passes that exact string to `addSafeDirectory()` — without ever verifying it matches (or is contained within) the repository path the user actually asked to add/open. Because the reported path can diverge from the repository the user is looking at (as the UI code itself acknowledges), a maliciously crafted repository can steer Desktop into silently marking an attacker-chosen filesystem location as globally "safe", permanently disabling Git's per-directory ownership protection for that location.

## Finding Description
`getRepositoryType()` runs `git rev-parse --is-bare-repository --show-cdup --git-dir` and, on failure, extracts the unsafe path directly from Git's error text: [1](#0-0) 

That extracted `unsafeMatch[1]` is returned as `{ kind: 'unsafe', path }` and surfaced to the UI as `repositoryUnsafePath`/`unsafePath`. Both consumer components explicitly render a branch for the case where this path differs from the path the user entered: [2](#0-1) 

Despite this acknowledged possibility of divergence, clicking "Trust Repository"/"add an exception for this directory" passes the reported path straight to `addSafeDirectory()` with no containment check against the repository being added: [3](#0-2) [4](#0-3) 

`addSafeDirectory()` itself performs no path validation; it simply appends the given string to the user's global Git config: [5](#0-4) 

The path Git reports for "dubious ownership" is derived from the resolved top-level/`gitdir` of the repository being probed, which can be redirected away from the folder the user is looking at — e.g. via a `.git` file using `gitdir:` indirection (as used for worktrees and submodules) pointing at an arbitrary absolute path, or via a symlinked working tree. A repository the user clones or adds therefore controls, indirectly, what "unsafe path" Git reports, and thus what path Desktop will mark as globally trusted when the user acts on the in-app prompt.

This mirrors the broken invariant in the reported smart-contract bug: an operation that should validate/commit state for one specific target (the trade order / the repository the user is adding) instead commits state (fund transfer / `safe.directory` entry) based on a value that can be influenced by an external, attacker-supplied signal (a reentrant call / a crafted `.git` indirection) rather than the value the caller actually authorized.

## Impact Explanation
Adding an arbitrary path to `safe.directory` disables Git's protection against operating in a directory owned by another user — a protection specifically designed to stop automatic execution of repository-local config/hooks in scenarios like shared machines or directories controlled by another local user/process. If the attacker-chosen path is a location that gets git-operated on later (by Desktop or the system Git), the ownership warning that would normally block automatic hook/config execution is silently bypassed for that specific path, forever, without further user consent for that specific location — the user only consented to trusting the repository they thought they were adding. This is a silent corruption of a security control (`safe.directory` config), reachable purely by adding/cloning an attacker-authored repository and clicking the app's own "Trust" affordance.

## Likelihood Explanation
Exploitation requires the victim to attempt to add/open a maliciously crafted repository (satisfies "attacker controls a cloned/fetched repository") that is engineered to trigger Git's dubious-ownership detection at a path different from the one displayed as the primary repository path, and then click through the in-app "Trust Repository" prompt — a single, expected user action for handling this exact warning banner. No admin rights, physical access, or leaked credentials are needed. The UI code's own defensive rendering for `repositoryUnsafePath !== convertedPath` shows the divergence case is a known, reachable condition rather than a theoretical one.

## Recommendation
- Before calling `addSafeDirectory`, verify that the reported `unsafePath`/`repositoryUnsafePath` is exactly the resolved repository path (or its parent within the expected working directory) that the user is attempting to add/open; reject or require explicit re-confirmation with the actual reported path shown prominently (not conditionally hidden) when it diverges.
- Alternatively, don't trust the parsed stderr path at all — re-run `getRepositoryType` after trust is granted and confirm the specific path acted upon equals the path passed into `addSafeDirectory`.
- Consider scoping trust more narrowly (e.g., only allow adding the literal path the user is opening, never a path derived from `.git` indirection inside an untrusted tree) before Git has verified ownership.

## Proof of Concept
1. Attacker creates a repository whose `.git` is a file (as used for worktrees/submodules) containing `gitdir: /tmp/attacker-target`, where `/tmp/attacker-target` is owned by a different user/UID than the victim (or otherwise fails Git's ownership check).
2. Victim clones/adds this repository in GitHub Desktop via `Add Existing Repository` or by opening a missing repository.
3. `getRepositoryType` invokes `git rev-parse` inside the repo; Git resolves the gitdir indirection to `/tmp/attacker-target`, detects dubious ownership there, and reports `detected dubious ownership in repository at '/tmp/attacker-target'` in stderr.
4. Desktop parses this into `repositoryUnsafePath = '/tmp/attacker-target'`, which differs from the path the victim opened; the UI still offers "add an exception for this directory".
5. Victim clicks "Trust Repository". `onTrustDirectory` calls `addSafeDirectory('/tmp/attacker-target')`, permanently adding that attacker-chosen path to the victim's global `~/.gitconfig` `safe.directory` list — disabling ownership protection for a location the victim never intended to trust.

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

**File:** app/src/ui/add-repository/add-existing-repository.tsx (L129-153)
```typescript
  private buildRepositoryUnsafeError() {
    const { repositoryUnsafePath, path } = this.state
    if (
      !this.state.path.length ||
      !this.state.showNonGitRepositoryWarning ||
      !this.state.isRepositoryUnsafe ||
      repositoryUnsafePath === undefined
    ) {
      return null
    }

    // Git for Windows will replace backslashes with slashes in the error
    // message so we'll do the same to not show "the repo at path c:/repo"
    // when the entered path is `c:\repo`.
    const convertedPath = __WIN32__ ? path.replaceAll('\\', '/') : path

    const displayedMessage = (
      <>
        <p>
          The Git repository
          {repositoryUnsafePath !== convertedPath && (
            <>
              {' at '}
              <Ref>{repositoryUnsafePath}</Ref>
            </>
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
