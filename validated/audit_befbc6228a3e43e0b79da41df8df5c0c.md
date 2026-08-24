### Title
`checkoutIndex` treats git exit code 1 as unconditional success, allowing "Discard Changes" to silently no-op on attacker-influenced paths - ([File: app/src/lib/git/checkout-index.ts])

### Summary
`checkoutIndex` calls `git checkout-index -f -u -q --stdin -z` with `successExitCodes: new Set([0, 1])` [1](#0-0) . Exit code `1` is blanket-accepted as "success" for *any* reason git returns it, not just the documented "path missing from index" case. This is the same class of bug as the ERC20 report: a boolean/status signal from an external, potentially adversarial-influenced operation is accepted at face value instead of being verified, so the caller believes an operation succeeded when it may have partially failed.

### Finding Description
`checkoutIndex` is Desktop's low-level primitive for restoring working-directory file contents from the index, and it is the final step of the "Discard Changes" flow, as documented: "The last step is to replace the modified files in the working directory with whatever is currently in the index... Git CLI equivalent: `git checkout-index -f -u -- [path]`" [2](#0-1) .

`git-store.ts`'s `discardChanges` builds a list of paths to reset and checkout and then calls `checkoutIndex` inside `performFailableOperation`, without inspecting per-file outcomes: [3](#0-2) . Because `checkoutIndex` only fails (throws) when the underlying `git()` call returns an exit code outside `{0, 1}`, and `git checkout-index` can legitimately exit with `1` for reasons unrelated to the "path not present in the index" case the `-q` flag is meant to suppress (e.g. the working-tree path being unwritable, colliding with a directory, being a symlink, or otherwise conflicting with attacker-controlled tree entries from a fetched/cloned repository), the caller has no way to know that some — or all — of the requested paths were never actually reverted to the index contents.

The core invariant that's broken is: "if `checkoutIndex` resolves without throwing, the working directory now matches the index for the requested paths." That invariant does not hold, because `successExitCodes: [0, 1]` was chosen to swallow one specific benign failure mode but ends up swallowing all exit-code-1 failures indiscriminately, with no verification (e.g. no post-hoc `git status`/hash comparison) that the discard actually took effect. Compare this to the `checkoutBranch`/`checkoutCommit` functions, which use the strict default `successExitCodes` and throw a `GitError` on any unexpected exit code [4](#0-3) ; `checkoutIndex` deliberately widens that acceptance set without a corresponding check of *what* was actually checked out.

### Impact Explanation
If an attacker crafts or manipulates a cloned/fetched repository so that discarding a specific file via `checkout-index` fails for that path (for example by exploiting filesystem-level path collisions, case-insensitivity quirks, or reserved/invalid filenames that are valid git tree entries but not valid filesystem paths on the victim's OS), Desktop's UI will report the discard as successful (no error thrown, `performFailableOperation` returns normally) while the file's working-directory content is left unchanged. A user who believes they discarded unwanted/malicious changes may then go on to stage and commit other files, or the leftover content may get silently included in a subsequent "select all" commit or push — i.e., the user's next commit/push can contain content they explicitly tried to discard, corrupting what they believe they are committing/pushing without any error being surfaced.

### Likelihood Explanation
This requires the victim to fetch/clone a repository or branch containing a file whose path is engineered to cause `git checkout-index` to fail on the victim's specific OS/filesystem semantics (case sensitivity, reserved names, path length, symlink/type conflicts) while still being a well-formed git tree entry, and for the victim to invoke Discard Changes on it. This is a real, no-privilege attacker-controlled path (repository content), but it depends on filesystem-specific edge cases to trigger the exit-code-1 failure path rather than a straightforward file. I could not fully verify from the available code/tests which specific `checkout-index` failure modes (beyond "path not in index") actually return exit code 1 versus a different code, since I did not have access to a live git invocation or exhaustive test coverage for `checkoutIndex`; the `reset-test.ts` reference to `checkout-index` was not inspected in detail.

### Recommendation
Do not widen `successExitCodes` to blanket-accept `1` for `checkout-index`. Instead:
1) Parse stderr/the git result to confirm the only failures were "does not exist in index" for `-q`, or
2) After the call, verify the actual working-directory state (e.g. re-run `getStatus`/diff on the requested paths) matches the index before treating the discard as successful, and surface an error/warning to the user for any path that could not be restored, analogous to using `safeTransferFrom`-style verification instead of ignoring the return value.

### Proof of Concept
Not independently reproduced (no filesystem/terminal access in this session). Conceptually:
1. Attacker publishes/pushes a branch/tree containing a file path known to trigger a `checkout-index` failure on the victim's OS while still being modifiable by the victim's working tree (e.g. via case-collision on case-insensitive filesystems, or by causing a type mismatch between a tracked file and an untracked directory at the same path).
2. Victim clones/fetches this repository in GitHub Desktop, modifies the conflicting path, then selects "Discard Changes" on that file.
3. `discardChangesFromSelection`/`discardChanges` → `checkoutIndex` runs `git checkout-index -f -u -q --stdin -z`; git exits with code `1` due to the underlying filesystem conflict.
4. Because `successExitCodes` includes `1`, `git()` returns normally, `performFailableOperation` reports success, and Desktop's UI shows the discard as complete with no error.
5. The file's on-disk content is unchanged; if the user subsequently commits/pushes (e.g. via "select all" or "commit all"), the discarded content is included, silently corrupting the resulting commit/push. [1](#0-0) [3](#0-2)

### Citations

**File:** app/src/lib/git/checkout-index.ts (L21-39)
```typescript
export async function checkoutIndex(
  repository: Repository,
  paths: ReadonlyArray<string>
) {
  if (!paths.length) {
    return
  }

  const options = {
    successExitCodes: new Set([0, 1]),
    stdin: paths.join('\0'),
  }

  await git(
    ['checkout-index', '-f', '-u', '-q', '--stdin', '-z'],
    repository.path,
    'checkoutIndex',
    options
  )
```

**File:** docs/technical/discard-changes.md (L35-41)
```markdown
### Checkout Paths

The last step is to replace the modified files in the working directory with
whatever is currently in the index - this ensures that Desktop only replaces
files that the user has chosen to discard.

**Git CLI equivalent**: `git checkout-index -f -u -- [path]`
```

**File:** app/src/lib/stores/git-store.ts (L1636-1648)
```typescript
    await this.performFailableOperation(async () => {
      if (submodulePaths.length > 0) {
        await resetSubmodulePaths(this.repository, submodulePaths)
      }

      await resetPaths(
        this.repository,
        GitResetMode.Mixed,
        'HEAD',
        necessaryPathsToReset
      )
      await checkoutIndex(this.repository, necessaryPathsToCheckout)
    })
```

**File:** app/src/lib/git/core.ts (L322-333)
```typescript
          const exitCode = result.exitCode

          let gitError: DugiteError | null = null
          const acceptableExitCode = opts.successExitCodes
            ? opts.successExitCodes.has(exitCode)
            : false
          if (!acceptableExitCode) {
            gitError = parseError(coerceToString(result.stderr))
            if (gitError === null) {
              gitError = parseError(coerceToString(result.stdout))
            }
          }
```
