## Title
Path traversal via crafted git status entry allows opening files outside the repository when "Open in External Editor" is used - (File: `app/src/ui/app.tsx`)

### Summary
The external report's underlying pattern is a broken invariant: a value derived from attacker-influenced input reaches a security-sensitive operation without passing through the guard that the rest of the codebase consistently applies elsewhere. In Desktop, the analogous invariant is "any repository-relative path that originates from data an attacker can influence (a cloned/fetched repository's tracked file paths) must be validated to stay inside the working directory before being used to build a filesystem path." That invariant is enforced in some code paths (e.g. `openRepositoryFromUrl` in the dispatcher) but not in `onOpenInExternalEditor`.

### Finding Description
`app/src/ui/app.tsx` builds the full filesystem path for "Open in External Editor" like this: [1](#0-0) 

`fullPath` is computed with a plain `Path.join(repository.path, path)` and passed straight to `dispatcher.openInExternalEditor`. There is no call to `resolveWithin`/`resolveWithinPosix`/`resolveWithinWin32` (the helper defined in `app/src/lib/path.ts`) which the codebase uses elsewhere specifically to stop path-traversal/symlink escapes: [2](#0-1) 

Compare this to the deep-link handler, which explicitly rejects absolute paths and resolves the target with `resolveWithin` before calling `shell.showItemInFolder`: [3](#0-2) 

The `path` argument that reaches `onOpenInExternalEditor` ultimately originates from file entries produced by parsing `git status --porcelain -z` output (`IStatusEntry.path`), which explicitly is *not* quoted/escaped by git in this format: [4](#0-3) [5](#0-4) 

Because `Path.join` collapses `..` segments arithmetically rather than rejecting them, a tracked path containing traversal segments (or a path/symlink structure crafted so a benign-looking relative path resolves outside the repo after `Path.join`) is joined directly onto `repository.path` with no containment check, no `realpath` verification, and no rejection of absolute-looking components — unlike `resolveWithin`, which additionally protects against symlink-based escapes via `realpath` comparison. This is the same broken invariant the report describes: an attacker-influenced value (there, share/asset accounting; here, a repository-controlled file path) reaches a sensitive sink without the guard that a parallel, correctly-guarded code path applies.

### Impact Explanation
If exploitable, this allows a cloned/fetched repository (attacker controls its tracked files/paths) to cause Desktop to invoke the user's configured external editor on a file path outside the repository working directory when the user interacts with the Changes list / "Open in External Editor" action for that entry. Depending on the external editor integration, this can range from disclosure of arbitrary file paths/content (opening a sensitive file in an editor "for the user to see") to more severe effects if the editor integration performs additional actions on the resolved path (e.g., some editor integrations execute helper commands against the path). This matches the report's "unprivileged, attacker controls a cloned/fetched repository, result is file read outside the repo" category.

### Likelihood Explanation
Likelihood is low-to-moderate and **not fully confirmed** from static analysis alone:
- It requires the user to open a malicious/untrusted repository in Desktop and to explicitly trigger "Open in External Editor" on the crafted path (a normal, expected user action, not an "unnatural step").
- It is unclear from the code reviewed here whether `git status --porcelain -z` paths can actually contain literal `..` traversal segments for files that are tracked/untracked in a normal working tree, or whether OS/Electron/git-level normalization would neutralize such a path before status-parser sees it. This would require testing directly against `dugite`/git behavior (e.g., via a crafted tree, submodule path, or symlinked working-directory entry) to confirm the primitive is reachable end-to-end — I could not verify this dynamically.
- The presence of a dedicated `resolveWithin` guard used elsewhere in the code strongly suggests the Desktop team is aware of and defends against exactly this class of bug, making it plausible this specific call site was simply missed.

### Recommendation
Route `onOpenInExternalEditor`'s path construction through `resolveWithin(repository.path, path)` (as already done in `dispatcher.ts`'s `openRepositoryFromUrl` for the `filepath` deep-link parameter), rejecting/no-oping when the resolved path is `null` or falls outside `repository.path`. Apply the same audit to any other call sites that build a full path via `Path.join(repository.path, <status/diff-derived path>)` without the `resolveWithin` guard.

### Proof of Concept
Not independently verified end-to-end (would require confirming that `git status --porcelain -z` can yield a path with `..` traversal reaching the UI unmodified, and that dugite/git does not itself sanitize or reject such paths). Conceptually:
1. Craft a repository whose git status output (or a symlinked/renamed working-tree entry) yields an `IStatusEntry.path` such as `../../../../.ssh/id_rsa` or an equivalent traversal string.
2. Get the victim to clone/open this repository in Desktop.
3. In the Changes list, select the crafted entry and invoke "Open in External Editor" (`onOpenInExternalEditor`).
4. `Path.join(repository.path, path)` in `app/src/ui/app.tsx` (lines 3429-3437) resolves outside the repository without any check, and `dispatcher.openInExternalEditor` opens that path. [1](#0-0)

### Citations

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

**File:** app/src/lib/status-parser.ts (L65-72)
```typescript
  // There is also an alternate -z format recommended for machine parsing. In that
  // format, the status field is the same, but some other things change. First,
  // the -> is omitted from rename entries and the field order is reversed (e.g
  // from -> to becomes to from). Second, a NUL (ASCII 0) follows each filename,
  // replacing space as a field separator and the terminating newline (but a space
  // still separates the status field from the first filename). Third, filenames
  // containing special characters are not specially formatted; no quoting or
  // backslash-escaping is performed.
```

**File:** app/src/lib/status-parser.ts (L105-119)
```typescript
function parseChangedEntry(field: string): IStatusEntry {
  const match = changedEntryRe.exec(field)

  if (!match) {
    log.debug(`parseChangedEntry parse error: ${field}`)
    throw new Error(`Failed to parse status line for changed entry`)
  }

  return {
    kind: 'entry',
    statusCode: match[1],
    submoduleStatusCode: match[2],
    path: match[8],
  }
}
```
