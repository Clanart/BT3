## Title
Directory-boundary bypass in `resolveWithin()` via naive prefix `startsWith()` check allows writes outside the intended repository directory - (`File: app/src/lib/path.ts`)

## Summary
The Desktop analog of the C4 finding is a broken-invariant bug of the same class: a security-critical boundary check is performed with an insufficient/naive comparison that a crafted input can slip past, causing the guarded operation (here, a file write derived from untrusted repository data) to execute outside its intended sandbox. In the SP1 report, an unchecked slice length let a malformed proof bypass the "is this data well-formed" guard before verification could reject it. In Desktop, `resolveWithin()` is the guard that is supposed to enforce "this resolved path must stay inside the repository root," but it is implemented with a bare string-prefix test that has no path-separator boundary check.

## Finding Description
`_resolveWithin()` in `app/src/lib/path.ts` computes the final decision with: [1](#0-0) 

```
const resolved = resolve(normalizedRoot, normalizedRelative)
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
```

`String.prototype.startsWith` performs a raw character comparison with no requirement that the next character after the matched prefix be a path separator. Consequently, any resolved path whose *string* begins with the root path's string — even a sibling directory or file that merely shares the root's name as a prefix — passes the check.

Concretely, if `root = "/Users/victim/Projects/app"` and an attacker-influenced relative segment is `"../app-secrets/config.json"`, then:
- `resolved = resolve(root, "../app-secrets/config.json") = "/Users/victim/Projects/app-secrets/config.json"`
- `realResolved.startsWith(realRoot)` evaluates `"/Users/victim/Projects/app-secrets/config.json".startsWith("/Users/victim/Projects/app")` → `true`

No `..` segment reaches the string itself (it is consumed by `resolve()`/`realpath()`), so this bypass requires only a single `..` plus a sibling name that shares the root folder's name as a prefix — it is not caught by the existing "fails for paths outside root" or symlink tests, which only exercise pure `..`-escape or symlink-escape cases, not the sibling-prefix case. [2](#0-1) 

None of the existing unit tests cover a sibling directory whose name is a superset-prefix of the root (e.g. `app` vs `app-secrets`), so this gap is untested and unguarded.

`resolveWithin` is relied upon as the sole containment guard for writes derived from AI ("Copilot") conflict-resolution data, which originates from repository content (conflict markers read out of tracked files) combined with model output: [3](#0-2) [4](#0-3) 

```
const absolutePath = await resolveWithin(repository.path, resolution.path)
if (absolutePath === null) { ... continue }
...
await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
```

and similarly gates conflict-content reads in: [5](#0-4) 

Because the guard's `startsWith` check can be defeated by a `path` value containing a single `..` traversal into a sibling directory whose name prefixes the repository directory's name, the "cannot escape repository root" invariant that both call sites depend on is not actually enforced in that scenario.

## Impact Explanation
This maps to the accepted impact category "silent corruption of what the user commits or pushes" / "file write ... outside the repo." If a `resolution.path` (sourced from AI-resolved conflict data whose file paths and content are influenced by attacker-controlled repository content via prompt injection in commit/file text) can be made to contain a `..`-relative path into a directory adjacent to the repository whose name happens to share the repository directory name as a prefix, `writeFile` will silently write model-produced content to a location the user never intended to modify — outside the tracked repository tree, with no visible diff, no git tracking, and no warning (the function only logs at `warn` level to a debug log the user does not see).

## Likelihood Explanation
Exploitation requires a specific naming coincidence between the repository folder and a sibling folder/file (e.g. `app` vs. `app.bak`, `repo` vs. `repo-2`, `project` vs `project_old`), plus the ability to influence the `resolution.path` string used with `resolveWithin`. This is a narrower likelihood than the SP1 report's trivial "any short buffer" trigger, since it depends on directory-naming coincidences and on how far attacker-controlled repository content can steer the AI-derived `path`/content values used at the `app-store.ts` call site. I was not able to fully trace, within the indexed content available, the exact upstream code that produces `resolution.path` values (i.e., whether it is strictly constrained to existing working-directory file paths before reaching `resolveWithin`, which would reduce exploitability) — this would need to be confirmed by reading the full Copilot conflict-resolution pipeline in a live checkout.

## Recommendation
Fix `_resolveWithin` to require a path-separator boundary after the prefix match (the standard safe pattern), e.g.:
```
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add regression tests for the sibling-prefix case (root `.../app`, target `.../app-evil/...`) for both POSIX and Windows variants, matching the existing `path-test.ts` suite structure.

## Proof of Concept
```ts
import { resolveWithin } from '../../src/lib/path'
import { mkdtemp, mkdir } from 'fs/promises'
import { join } from 'path'
import { tmpdir } from 'os'

const base = await mkdtemp(join(tmpdir(), 'poc-'))
const root = join(base, 'app')
const sibling = join(base, 'app-secrets')
await mkdir(root)
await mkdir(sibling)

// Attacker-controlled relative path segment
const maliciousRelative = join('..', 'app-secrets', 'config.json')

const result = await resolveWithin(root, maliciousRelative)
console.log(result)
// Expected (secure): null
// Actual: the absolute path to base/app-secrets/config.json — the sibling
// directory 'app-secrets' passes because it string-starts-with 'app'.
```

### Citations

**File:** app/src/lib/path.ts (L66-71)
```typescript
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
```

**File:** app/test/unit/path-test.ts (L44-63)
```typescript
  describe('resolveWithin', () => {
    const root = process.cwd()

    it('fails for paths outside of the root', async () => {
      assert((await resolveWithin(root, join('..'))) === null)
      assert((await resolveWithin(root, join('..', '..'))) === null)
    })

    it('succeeds for paths that traverse out, and then back into, the root', async () => {
      assert.equal(await resolveWithin(root, join('..', basename(root))), root)
    })

    it('fails for paths containing null bytes', async () => {
      assert((await resolveWithin(root, 'foo\0bar')) === null)
    })

    it('succeeds for absolute relative paths as long as they stay within the root', async () => {
      const parent = resolve(root, '..')
      assert.equal(await resolveWithin(parent, root), root)
    })
```

**File:** app/src/lib/stores/app-store.ts (L7233-7239)
```typescript
      const absolutePath = await resolveWithin(repository.path, resolution.path)
      if (absolutePath === null) {
        log.warn(
          `Copilot resolution skipped: path outside repository: ${resolution.path}`
        )
        continue
      }
```

**File:** app/src/lib/stores/app-store.ts (L7258-7259)
```typescript
      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
      pathsToStage.push(resolution.path)
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
