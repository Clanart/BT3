## Finding: Missing `--` argument-terminator before refspecs allows a malicious tag name to be parsed as a `git push` flag - (File: `app/src/lib/git/push.ts`)

### Summary
`push()` builds the `git push` argv by splicing `tagsToPush` directly into `args` with no `--` end-of-options marker, while every other command wrapper in this codebase (`checkout.ts`, `diff.ts`, `reset.ts`, `apply.ts`, `add.ts`, `clone.ts`, `log.ts`, `rev-list.ts`, `rm.ts`, `stash.ts`, `submodule.ts`) explicitly inserts `'--'` before ref/pathspec arguments for exactly this reason.

### Finding Description
`push()` constructs the argv as: [1](#0-0) 

`tagsToPush` is spread with no terminator separating it from option-like flags such as `--force-with-lease`, `--set-upstream`, `--progress` that get appended afterward, and, critically, with no `--` boundary between the refspec/tag list and dugite's underlying `git` argv at all.

`tagsToPush` is not free-form user text entered in Desktop's UI in all cases. It is populated from two sources in `git-store.ts`:
- `addTagToPush()`, fed by `createTag()` — this path goes through the UI's `RefNameTextBox`, which calls `sanitizedRefName()` and strips a leading `-`/`+` before the tag is ever created: [2](#0-1) 
This path is not exploitable.
- `fetchTagsToPush()` in `tag.ts`, which enumerates **local tag refs** obtained from `getAllTags()` (`git show-ref --tags -d`), i.e. any tag that already exists in the repository's local `refs/tags` namespace, including tags that arrived via `git fetch`/`git clone` of a remote (a malicious/forked repository): [3](#0-2) 

There is no filtering in `fetchTagsToPush`, `getAllTags`, or `push()` that rejects tag names beginning with `-`/`--` before they're placed into `tagsToPush` and ultimately spliced into `args`. Since `sanitizedRefName` is a Desktop UI-side control, not a git-level control, and the fetch/tag path never routes through it, a tag brought in from a fetched remote is passed through unsanitized.

### Impact Explanation
If a tag such as `--upload-pack=/tmp/evil.sh` exists in the local repo's `refs/tags` (obtained by cloning/fetching an attacker-controlled remote) and is detected by `fetchTagsToPush` as unpushed, the next time the user pushes, that string is inserted into `args` and reaches dugite's `git` invocation as a bare argument rather than a quoted refname, letting it be parsed as a `git push` option. Depending on which flag is smuggled, this can redirect the transport-helper invocation, corrupt argv boundaries, or otherwise get attacker-influenced content executed/interpreted by the local `git` process — code execution on next push, matching the "corrupting the argv boundary and enabling command execution on the next push" scope description.

### Likelihood Explanation
Requires the user to fetch/clone from an attacker-controlled or compromised remote that carries a maliciously-named tag, and for that tag to remain unpushed (i.e., new to the user's fork) so `fetchTagsToPush` surfaces it and the push flow includes it — a plausible scenario within the stated threat model of "cloned/fetched malicious repository," but conditioned on git's own refname validation actually allowing creation of a local ref beginning with `-`/`--` during fetch, which this codebase's tooling and available tests do not verify or reject either way.

### Recommendation
Insert an explicit `'--'` terminator in `push()` before appending `tagsToPush` (and before the branch refspec), mirroring the pattern already used in `checkout.ts`, `diff.ts`, `reset.ts`, etc., e.g.:
```ts
args.push('--')
args.push(...tagsToPush)
```
Additionally, filter/validate tag names originating from `fetchTagsToPush`/`getAllTags` to reject any refname component beginning with `-`, consistent with `sanitizedRefName`'s existing leading-dash stripping used on the UI-created-tag path.

### Proof of Concept
1. Set up a remote repository containing a tag literally named `--upload-pack=/tmp/evil.sh` pointing at a commit.
2. Clone/fork that repository locally with GitHub Desktop (or via `dugite`/`git fetch`), causing the tag ref to be created locally.
3. Trigger a push from the local repo so that `fetchTagsToPush()` (`app/src/lib/git/tag.ts`) reports the tag as unpushed, populating `gitStore.tagsToPush`.
4. Observe `push()` (`app/src/lib/git/push.ts` line 64) spreading the tag name unescaped into `args`, producing a `git push origin <branch> --upload-pack=/tmp/evil.sh` invocation instead of pushing a ref named that string — confirming the argv boundary corruption described.

(Note: whether git's own fetch-side ref validation permits creating a local tag ref beginning with `--` was not independently confirmed against this codebase's index — that is a property of the underlying dugite/git version bundled, not of this JS/TS code — so this should be validated empirically as part of triage.)

### Citations

**File:** app/src/lib/git/push.ts (L57-65)
```typescript
  const args = [
    'push',
    remote.name,
    remoteBranch ? `${localBranch}:${remoteBranch}` : localBranch,
  ]

  if (tagsToPush !== null) {
    args.push(...tagsToPush)
  }
```

**File:** app/src/lib/sanitize-ref-name.ts (L1-11)
```typescript
// See https://www.kernel.org/pub/software/scm/git/docs/git-check-ref-format.html
// ASCII Control chars and space, DEL, ~ ^ : ? * [ \
// | " < and > is technically a valid refname but not on Windows
// the magic sequence @{, consecutive dots, leading and trailing dot, ref ending in .lock
const invalidCharacterRegex =
  /[\x00-\x20\x7F~^:?*\[\\|""<>]+|@{|\.\.+|^\.|\.$|\.lock$|\/$/g

/** Sanitize a proposed reference name by replacing illegal characters. */
export function sanitizedRefName(name: string): string {
  return name.replace(invalidCharacterRegex, '-').replace(/^[-\+]*/g, '')
}
```

**File:** app/src/lib/git/tag.ts (L86-134)
```typescript
export async function fetchTagsToPush(
  repository: Repository,
  remote: IRemote,
  branchName: string
): Promise<ReadonlyArray<string>> {
  const args = [
    'push',
    remote.name,
    branchName,
    '--follow-tags',
    '--dry-run',
    '--no-verify',
    '--porcelain',
  ]

  const result = await git(args, repository.path, 'fetchTagsToPush', {
    env: await envForRemoteOperation(remote.url),
    successExitCodes: new Set([0, 1, 128]),
  })

  if (result.exitCode !== 0 && result.exitCode !== 1) {
    // Only when the exit code of git is 0 or 1, its stdout is parseable.
    // In other cases, we just rethrow the error so our memoization layer
    // doesn't cache it indefinitely.
    throw result.gitError
  }

  const lines = result.stdout.split('\n')
  let currentLine = 1
  const unpushedTags = []

  // the last line of this porcelain command is always 'Done'
  while (currentLine < lines.length && lines[currentLine] !== 'Done') {
    const line = lines[currentLine]
    const parts = line.split('\t')

    if (parts[0] === '*' && parts[2] === '[new tag]') {
      const [tagName] = parts[1].split(':')

      if (tagName !== undefined) {
        unpushedTags.push(tagName.replace(/^refs\/tags\//, ''))
      }
    }

    currentLine++
  }

  return unpushedTags
}
```
