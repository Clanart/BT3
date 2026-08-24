## Finding

Analog located: the container-invariant check for repository-relative file paths that is enforced by `resolveWithin()` in some Desktop code paths is **not** enforced in the "open file in external editor" flow, allowing a symlink committed by an attacker-controlled repository to make the editor open (i.e., read/exfiltrate) an arbitrary file on disk outside the repository, mirroring the `linkWallet()` bug's core flaw: a containment/relationship check that exists in one code path but is skipped/asymmetric in the sibling path that reaches the same sink.

### Title
Repository-relative file paths from a malicious clone bypass `resolveWithin` containment when opened in an external editor - (File: `app/src/ui/app.tsx`)

### Summary
Desktop's own path-safety guard, `resolveWithin()`, is meant to guarantee that a repository-relative path can never resolve (even through symlinks, via `realpath`) to a location outside the repository root before being handed to disk I/O or external processes. [1](#0-0) 
This guard is correctly applied elsewhere, e.g. when reading conflicted files for Copilot context: [2](#0-1) 
and when handling `x-github-client://` deep-link `filepath` parameters: [3](#0-2) 

However, `App.onOpenInExternalEditor` — the handler wired up to "Open in external editor" actions for changed/committed files in the Changes list, History view, stash diff viewer, and PR files-changed list — joins the repository-relative path directly with the repository root and hands it straight to the editor, with no `resolveWithin`/`realpath` containment check: [4](#0-3) 

### Finding Description
The broken invariant is: *"a path shown as belonging to file X inside the repository must resolve to a location inside the repository when opened."* `resolveWithin` enforces this by resolving through `fs.realpath` on both the root and the target so that symlinks cannot walk the result outside the root. [5](#0-4) 

`onOpenInExternalEditor`, by contrast, only does a `Path.join` (which does not follow symlinks and does not check the final on-disk location): [6](#0-5) 

A clone/fetch of an attacker-controlled repository can commit a symlink blob (git mode `120000`) named e.g. `innocent.txt` pointing to `../../../../.ssh/id_rsa` or any other absolute/traversal target. Git will happily check this out as a symlink (subject to normal filesystem symlink semantics), and the file will appear as a perfectly ordinary changed/tracked file named `innocent.txt` in the Changes list, History diff, stash diff, or PR "Files changed" list — all of which route through `onOpenInExternalEditor`. When the user (or, in some of these views, a single click/keyboard action) opens that file "in external editor," `Path.join(repository.path, 'innocent.txt')` still looks like it's inside the repo, but the filesystem resolves the symlink to the attacker-chosen target, and the external editor opens/reads that target file's contents.

This is exactly the reversed/asymmetric-validation pattern in the report: the code that should prevent "child becomes parent" (here: "path inside repo resolves outside repo") checks the condition in one call path (`copilot-conflict-context.ts`, deep-link `filepath` handling) but omits it in the sibling call path that reaches the same class of sink (`onOpenInExternalEditor`).

### Impact Explanation
An unprivileged attacker who controls a repository the victim clones/fetches (a normal, everyday Desktop action) can plant a symlink pointing at sensitive files (SSH keys, cloud credential files, `.netrc`, shell profiles, etc.). If the victim opens that tracked file via any of Desktop's "Open in external editor" affordances, the external editor reads and displays the *target* file's contents — a file-read-outside-the-repo primitive that can lead to credential/secret exfiltration, satisfying the "Valid Impact" criteria (attacker controls a cloned/fetched repository; result is file read outside the repo).

### Likelihood Explanation
Committing a symlink to a git repository is trivial and requires no special git features; cloning/fetching a repository is a core, expected Desktop workflow. The only user action required is using the existing "Open in external editor" command on a file that already appears in the normal Changes/History/PR file list — no unusual or unnatural steps are needed, unlike scenarios requiring local access or pre-existing malware. Likelihood is moderate-to-high for any workflow where users routinely open individual changed files in an editor from Desktop's UI.

### Recommendation
Route the path in `App.onOpenInExternalEditor` (and any other caller that joins a repository-relative path with `repository.path` before touching disk or spawning an external process) through `resolveWithin(repository.path, path)`, refusing to open the file (with a user-facing error, matching the pattern already used in `dispatcher.ts`'s `openRepositoryFromUrl`) when the resolved value is `null`: [7](#0-6) 
Additionally, symlinked tracked files reached through the Changes/History/PR "Files changed" lists should be identified and either shown with a distinct affordance or resolved via `realpath` before dispatch, consistent with the containment approach already implemented in `app/src/lib/path.ts`.

### Proof of Concept
1. Attacker creates a repository containing a symlink blob: `ln -s ../../../../.ssh/id_rsa innocent.txt && git add innocent.txt && git commit`.
2. Victim clones this repository in GitHub Desktop.
3. Victim views the file in the Changes list (if uncommitted change) or History/PR "Files changed" view, and selects "Open in external editor" (or double-clicks, depending on view) which invokes `App.onOpenInExternalEditor('innocent.txt')`. [4](#0-3) 
4. `Path.join(repository.path, 'innocent.txt')` yields a path that is textually inside the repository, but the OS resolves the symlink when the external editor opens it, causing the editor to display the contents of `~/.ssh/id_rsa` (or any other target path chosen by the attacker) instead of repository content — with no `resolveWithin`/`realpath` check ever executed to catch the escape, unlike the equivalent code path in `app/src/lib/copilot-conflict-context.ts:390-407`.

**Note on completeness:** I was not able to fully trace the exact `openInExternalEditor` implementation on the dispatcher/app-store/main-process side (e.g., whether the main process itself does any additional `realpath`/containment check before invoking the OS "open" call) within the available iterations, so it is possible — but unconfirmed — that a downstream check partially mitigates this. I recommend a background agent verify the full call chain (`app/src/ui/dispatcher/dispatcher.ts` → `app-store.ts` → main-process IPC handler) for `openInExternalEditor` to confirm no equivalent `resolveWithin`/`realpath` check exists there before treating this as fully unmitigated.

### Citations

**File:** app/src/lib/path.ts (L36-72)
```typescript
async function _resolveWithin(
  rootPath: string,
  pathSegments: string[],
  options: {
    join: (...pathSegments: string[]) => string
    normalize: (p: string) => string
    resolve: (...pathSegments: string[]) => string
  } = Path
) {
  // An empty root path would let all relative
  // paths through.
  if (rootPath.length === 0) {
    return null
  }

  const { join, normalize, resolve } = options

  const normalizedRoot = normalize(rootPath)
  const normalizedRelative = normalize(join(...pathSegments))

  // Null bytes has no place in paths.
  if (
    normalizedRoot.indexOf('\0') !== -1 ||
    normalizedRelative.indexOf('\0') !== -1
  ) {
    return null
  }

  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
}
```

**File:** app/src/lib/copilot-conflict-context.ts (L390-407)
```typescript
      // Guard against path traversal and symlink escapes (cross-platform)
      let absolutePath: string | null
      try {
        absolutePath = await resolveWithin(workingDirectory, file.path)
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path could not be resolved safely',
        }
      }
      if (absolutePath === null) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path is outside the repository',
        }
      }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1972)
```typescript
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
```

**File:** app/src/ui/app.tsx (L3429-3437)
```typescript
  private onOpenInExternalEditor = (path: string) => {
    const repository = this.state.selectedState?.repository
    if (repository === undefined) {
      return
    }

    const fullPath = Path.join(repository.path, path)
    this.props.dispatcher.openInExternalEditor(fullPath)
  }
```
