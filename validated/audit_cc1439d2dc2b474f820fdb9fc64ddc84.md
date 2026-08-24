## Title
`resolveWithin`'s sandbox check uses an un-anchored `startsWith` prefix comparison, allowing attacker-controlled paths (e.g. deep link / dispatcher targets) to escape the intended root directory into a sibling folder - (File: `app/src/lib/path.ts`)

## Summary
The report's bug class is: a boundary check that is *structurally correct in form* ("only allow X at/under the intended limit") but is *implemented as a naive comparison that doesn't verify the true containment relationship*, letting the guarded operation land outside the intended boundary. The Desktop analog is `resolveWithin()` / `resolveWithinPosix()` / `resolveWithinWin32()` in [1](#0-0) , whose containment check is `realResolved.startsWith(realRoot)` [2](#0-1) . Just like `minUsdAmountOut < toUsdAmount(boostAmount)` only checks an endpoint value instead of the real invariant ("price must stay at or above 1:1"), `startsWith(realRoot)` only checks a string prefix instead of the real invariant ("resolved path must be `realRoot` or a path segment beneath it").

## Finding Description
`_resolveWithin` is documented as guaranteeing "the resolved path is guaranteed to reside at, or underneath" `rootPath` [3](#0-2) . It resolves the root and the caller-supplied segments, canonicalizes both via `realpath`, and then performs:

```
return realResolved.startsWith(realRoot) ? resolved : null
```

`String.prototype.startsWith` has no notion of path-segment boundaries. If `realRoot` is `/Users/victim/repo`, then `realResolved` values such as `/Users/victim/repo-evil` or `/Users/victim/repository-2` also satisfy `realResolved.startsWith(realRoot)`, even though neither path is actually inside `repo`. This is the exact same class of defect as the Boost report: the guard checks a value that correlates with the invariant in the common case but does not enforce the invariant itself, so intermediate/edge inputs slip through the "if" check unpunished.

This helper is consumed by callers that resolve attacker/remote-influenced path fragments against a trusted root, e.g. `app/src/ui/dispatcher/dispatcher.ts` and `app/src/lib/stores/app-store.ts`, which call `resolveWithin`/`win32.resolveWithin`/`posix.resolveWithin` to keep deep-link or repository-derived paths confined to the local repository/working directory. Because the check is a raw prefix match, any sibling directory that happens to share the root directory name as a prefix (a name an attacker can arrange for, e.g. by naming a cloned/fetched repo or a deep-link target `repo-something`) bypasses the containment guarantee the function promises to its callers.

## Impact Explanation
Callers rely on `resolveWithin`'s `null`-on-escape contract to decide whether a path derived from untrusted input (deep link parameters, repository-relative paths) is safe to use for file reads/writes or to hand off to further operations. A prefix-only check means the function can return a *non-null, seemingly validated* path that is actually outside the intended root — silently defeating the sandbox the callers believe they have. Depending on which caller consumes the escaped path, this can translate into file reads/writes outside the repository, which matches the "file write or read outside the repo" impact category called out as valid for this task. This mirrors the AMO case where the guard's failure mode wasn't a crash but a silent, financially/functionally incorrect outcome that looked like it had passed validation.

## Likelihood Explanation
The bypass only requires an attacker to control the *name* of a sibling path segment (e.g., a repository name, a deep-link path fragment) so that `basename(root) + "suffix"` is realpath-resolvable and shares the root's string prefix — no local/physical access, no admin rights, and no pre-existing malware are required, satisfying the task's validity constraints. The existing unit tests in `app/test/unit/path-test.ts` only exercise `..`-traversal and symlink-traversal cases [4](#0-3) ; there is no test covering the sibling-directory-prefix case, indicating this specific bypass path is untested and unguarded.

## Recommendation
Replace the prefix string comparison with a proper containment check that verifies a path-separator boundary, e.g.:
```ts
return realResolved === realRoot ||
  realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
This is the direct fix analogous to the audit's recommendation to check the true balanced/contained condition rather than a loose comparison.

## Proof of Concept
```ts
import { resolveWithin } from '../../src/lib/path'
// realRoot resolves to /tmp/xyz/repo
// attacker arranges for a sibling directory /tmp/xyz/repo-evil to exist
// (e.g., via a second cloned/fetched repository or extracted archive)
const result = await resolveWithin('/tmp/xyz/repo', '../repo-evil/secret.txt')
// Expected: null (outside root)
// Actual (with current implementation): non-null path, because
// '/tmp/xyz/repo-evil/secret.txt'.startsWith('/tmp/xyz/repo') === true
```

Note: I was unable to fully trace, within the available context, exactly which untrusted input reaches `resolveWithin` in `dispatcher.ts`/`app-store.ts`/`copilot-conflict-context.ts` (full call sites weren't retrievable in this pass), so the precise end-to-end trigger (deep link vs. repo-derived path) should be confirmed against those call sites before treating this as fully proven exploitable; the flaw in the containment check itself, however, is directly verifiable in `app/src/lib/path.ts`.

### Citations

**File:** app/src/lib/path.ts (L13-24)
```typescript
/**
 * Resolve one or more path sequences into an absolute path underneath
 * or at the given root path.
 *
 * The path segments are expected to be relative paths although
 * providing an absolute path is also supported. In the case of an
 * absolute path segment this method will essentially only verify
 * that the absolute path is equal to or deeper in the directory
 * tree than the root path.
 *
 * If the fully resolved path does not reside underneath the root path
 * this method will return null.
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

**File:** app/test/unit/path-test.ts (L47-100)
```typescript
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

    if (!__WIN32__) {
      it('fails for paths that use a symlink to traverse outside of the root', async () => {
        const tempDir = await mkdtemp(join(tmpdir(), 'path-test'))
        const symlinkName = 'dangerzone'
        const symlinkPath = join(tempDir, symlinkName)

        try {
          await symlink(resolve(tempDir, '..', '..'), symlinkPath)
          assert((await resolveWithin(tempDir, symlinkName)) === null)
        } finally {
          await unlink(symlinkPath)
          await rmdir(tempDir)
        }
      })

      it('succeeds for paths that use a symlink to traverse outside of the root and then back again', async () => {
        const tempDir = await mkdtemp(join(tmpdir(), 'path-test'))
        const symlinkName = 'dangerzone'
        const symlinkPath = join(tempDir, symlinkName)

        try {
          await symlink(resolve(tempDir, '..', '..'), symlinkPath)
          const throughSymlinkPath = join(
            symlinkName,
            basename(resolve(tempDir, '..')),
            basename(tempDir)
          )
          assert.equal(
            await resolveWithin(tempDir, throughSymlinkPath),
            resolve(tempDir, throughSymlinkPath)
          )
        } finally {
          await unlink(symlinkPath)
          await rmdir(tempDir)
        }
      })
```
