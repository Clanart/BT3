### Title
Missing path-traversal validation in `onOpenInExternalEditor` allows opening files outside the repository - (File: `app/src/ui/app.tsx`)

### Summary
The external report's core issue is "an unvalidated, attacker-influenced input value is trusted and used to construct a sensitive resource reference without a bounds/allow-list check." The closest Desktop analog is the file-path handling that feeds the "Open in External Editor" action for individual files. Unlike the sibling deep-link handler for the same concept, this code path performs no containment check.

### Finding Description
When a file is opened from a changed-files/diff list (e.g. Changes sidebar, commit history, PR "Files changed" view), the UI layer resolves it with: [1](#0-0) 

`path` is joined directly onto `repository.path` with `Path.join()` and handed to `dispatcher.openInExternalEditor(fullPath)` with **no check that the resolved path stays inside the repository**. This is notably inconsistent with the equivalent, already-hardened code path for the `filepath` parameter of the "Open in Desktop" deep link, which explicitly rejects absolute paths and calls `resolveWithin()` to enforce containment before touching the filesystem: [2](#0-1) 

The project has a purpose-built, symlink-aware containment primitive for exactly this situation: [3](#0-2) 

but `onOpenInExternalEditor` does not use it — it relies on bare `Path.join`, which normalizes but does not reject `..` traversal segments, and performs no `isAbsolute()` guard the way the deep-link handler does.

### Impact Explanation
If a `path` value reaching `onOpenInExternalEditor` can contain `../` traversal segments (e.g. via a crafted diff/file entry rendered in the Changes list, commit view, or the PR "Files changed" panel), `Path.join(repository.path, path)` can resolve to a location outside the repository working directory, and the app will launch the user's configured external editor pointed at that path — a file-read-outside-the-repo primitive satisfying the report's "unvalidated address/reference used to reach unintended resource" pattern. This is the same bug class as the external report (an unchecked identifier is trusted to reference a resource without verifying it is the intended, safe one), translated to Desktop's file-path/URL-handling domain rather than a token-address domain.

### Likelihood Explanation
This is rated as **uncertain/needs further verification** rather than confirmed exploitable: normal git tree entries cannot contain a literal `..` path component (git's own tree/checkout validation rejects this, a protection added after CVE-2014-9390-class issues), so it is not established from the code available here that a cloned/fetched repository's tracked file paths can actually smuggle `../` into this specific `path` argument. The vulnerable *pattern* (missing containment check) is confirmed by direct code inspection and by contrast with the guarded sibling code path in `dispatcher.ts`, but I could not fully trace every caller (e.g. `pull-request-files-changed.tsx`, `selected-commits.tsx`) back to its ultimate untrusted data source within the available tool budget to confirm an end-to-end attacker-controlled trigger.

### Recommendation
Apply the same guard used in `dispatcher.ts` for deep-link filepaths: reject absolute paths and route the join through `resolveWithin(repository.path, path)` before calling `dispatcher.openInExternalEditor`, returning/logging on `null` instead of opening the editor.

### Proof of Concept
Not fully constructible from local code alone — this requires confirming a concrete source in this codebase that can produce a `path` value containing `../` segments before it reaches `app.tsx:3429` (`onOpenInExternalEditor`). I was not able to conclusively confirm such a source within the tool budget available; a background Devin session with full repository/test access would be needed to trace all callers (`repository.tsx`, `open-pull-request-dialog.tsx`, `selected-commits.tsx`, `sidebar.tsx`, `pull-request-files-changed.tsx`) back to their data origin and construct a working end-to-end PoC.

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
