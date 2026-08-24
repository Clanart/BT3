### Title
Path-containment check in `resolveWithin` uses unbounded `String.prototype.startsWith`, allowing sibling-directory escape - (File: `app/src/lib/path.ts`)

### Summary
`_resolveWithin()` in `app/src/lib/path.ts` validates that a resolved path is contained within a root directory by testing `realResolved.startsWith(realRoot)`. This is the same class of bug as the reported Pump Science issue: an invariant check compares two values without accounting for a boundary condition that must be excluded from the comparison, so the check "passes" in cases where it should fail. Here the missing boundary is a path separator: any resolved path whose string representation begins with the same characters as the root — including a completely different sibling directory such as `<root>-evil` — satisfies `startsWith(realRoot)` even though it is not actually nested inside the root.

### Finding Description
`resolveWithin` is the app's designated guard for "this path must stay inside the repository/root directory," used for attacker-influenced inputs such as deep-link `x-github-client://` file paths (`app/src/ui/dispatcher/dispatcher.ts`, `resolveWithin(repository.path, filepath)`) and Copilot conflict-resolution file writes (`app/src/lib/copilot-conflict-context.ts`, `app/src/lib/stores/app-store.ts`).

The containment check itself: [1](#0-0) 

```
const resolved = resolve(normalizedRoot, normalizedRelative)
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
```

`realResolved.startsWith(realRoot)` is a raw string-prefix comparison with no trailing separator appended to `realRoot`. If `realRoot` is `/Users/victim/Documents/GitHub/myrepo` and an attacker can cause `resolved`/`realResolved` to be a sibling path such as `/Users/victim/Documents/GitHub/myrepo-secrets/id_rsa`, the string `.../myrepo-secrets/id_rsa` still starts with `.../myrepo`, so the check incorrectly returns the resolved (out-of-root) path instead of `null`.

This directly parallels the Pump Science bug: `sol_escrow_lamports < bonding_curve.real_sol_reserves` silently passes because the comparison ignores an excluded quantity (rent); here `realResolved.startsWith(realRoot)` silently passes because the comparison ignores the path-separator boundary that should be required immediately after `realRoot`.

Elsewhere in the codebase the correct, separator-aware pattern is already known and used, e.g. in `app/src/lib/git/clone.ts`: [2](#0-1) 
```
if (clonePath === sensitive || clonePath.startsWith(sensitive + Path.sep)) {
    return true
}
```
This shows the developers are aware that `startsWith` alone is unsafe for containment checks and add `+ Path.sep` — but that fix was not applied to `resolveWithin`, which is the one function whose entire purpose (per its docstring) is to be "guaranteed to reside at, or underneath" the root.

### Impact Explanation
`resolveWithin` is a security boundary relied on to prevent path traversal from attacker-controlled data (URL deep-link file paths, Copilot-generated conflict-resolution file paths). If an attacker can make the resolved target land in a sibling directory whose name has the root directory's name as a string prefix (e.g. a repository named `repo` cloned next to a directory `repo-backup`, or two repos `foo` and `foobar` under the same parent), the guard will incorrectly treat paths in the sibling directory as "inside the repo," permitting:
- `shell.showItemInFolder` to reveal/open a file outside the intended repository via `dispatcher.ts`'s `openRepositoryFromUrl` deep-link handler.
- Writing attacker/Copilot-resolved file content to a path outside the intended repository via `_finalizeCopilotConflictResolution` in `app-store.ts`, potentially overwriting files in an unrelated directory that merely shares a name prefix with the repo root.

This matches the report's required impact class: file write/read outside the intended repo boundary triggered by attacker-influenced input (a crafted deep link / crafted file path).

### Likelihood Explanation
Exploitability depends on the existence of a sibling directory whose path is a string-prefix superset of the root path (e.g., `Documents/GitHub/myrepo` and `Documents/GitHub/myrepo2`), which is a common real-world directory layout for developers who clone multiple related repositories. The `x-github-client://openLocalRepo` deep link already accepts an attacker/webpage-controlled `filepath` parameter and is explicitly guarded only against absolute paths, then handed to `resolveWithin` — no separator-boundary protection exists for relative traversal that resolves to a same-prefixed sibling. This is a no-privilege, remote-triggerable path (clicking a link), matching the required threat model.

### Recommendation
Change the containment check to require a path separator (or exact equality) immediately after the root, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
using the same separator the active `options` (`Path`, `Path.posix`, or `Path.win32`) would produce, mirroring the fix already applied in `isClonePathSensitive` in `app/src/lib/git/clone.ts`.

### Proof of Concept
1. Create `Documents/GitHub/myrepo` (a Desktop repository) and `Documents/GitHub/myrepo-secrets/token.txt` (unrelated sibling directory).
2. Trigger the `x-github-client://openLocalRepo?url=...&filepath=../myrepo-secrets/token.txt` deep link (or any code path calling `resolveWithin(repository.path, filepath)` in `app/src/ui/dispatcher/dispatcher.ts`) with `filepath` set to a relative traversal that resolves to `Documents/GitHub/myrepo-secrets/token.txt`.
3. In `_resolveWithin` (`app/src/lib/path.ts:66-71`), `realResolved` becomes `Documents/GitHub/myrepo-secrets/token.txt`; `realRoot` is `Documents/GitHub/myrepo`. `realResolved.startsWith(realRoot)` evaluates `true` because the string `"...myrepo-secrets/token.txt"` begins with `"...myrepo"`, even though `myrepo-secrets` is not a subdirectory of `myrepo`.
4. `resolveWithin` returns the out-of-root path instead of `null`, and `shell.showItemInFolder(resolved)` (or a Copilot file write) operates on a file outside the repository.

Note: I was not able to fully trace every call site's exact upstream input-sanitization (e.g., how strictly `filepath` from the deep-link handler is otherwise constrained before reaching `resolveWithin`) using the available index; a Devin session with full repo access would be needed to confirm there is no earlier normalization step that already blocks a sibling-prefix path in the deep-link flow specifically.

### Citations

**File:** app/src/lib/path.ts (L64-71)
```typescript
  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
```

**File:** app/src/lib/git/clone.ts (L40-44)
```typescript
  for (const sensitive of sensitiveLocations) {
    if (clonePath === sensitive || clonePath.startsWith(sensitive + Path.sep)) {
      return true
    }
  }
```
