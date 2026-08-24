## Title
Unbounded memory allocation when buffering `git` output for buffer-encoded commands - ([File: app/src/lib/git/core.ts])

### Summary
GitHub Desktop's `git()` wrapper enforces a hard cap on stdout/stderr size (`kStringMaxLength`, Node's max string length) for string-encoded git commands, but explicitly disables that cap — setting `maxBuffer: Infinity` — for any command executed with `encoding: 'buffer'`. Several buffer-encoded git commands (`git status`, `git log`/`getCommits`, `git diff` variants, `git show`) operate directly on attacker-influenced repository content (a cloned/fetched malicious repository), so a repository can be crafted to make git emit an arbitrarily large amount of output on one of these buffer-encoded code paths, causing the Electron main process to buffer that output entirely in memory with no upper bound.

### Finding Description
In `app/src/lib/git/core.ts`, the default execution options are: [1](#0-0) 

```
const defaultOptions: IGitExecutionOptions = {
  successExitCodes: new Set([0]),
  expectedErrors: new Set(),
  maxBuffer: options?.encoding === 'buffer' ? Infinity : kStringMaxLength,
}
```

For string-encoded output, `maxBuffer` is `kStringMaxLength` (Node's ~1GB string cap), which is the guard that a prior fix (changelog "Cap output from git at string max length" / "Prevent crash due to excessively long Git output", #19724) added. But for any command run with `encoding: 'buffer'`, this protection is intentionally bypassed and `maxBuffer` becomes `Infinity`, meaning dugite's underlying `execFile`/spawn will keep accumulating stdout/stderr into memory without any limit.

This buffer-encoded path is used by several git operations that run on cloned/fetched, potentially attacker-controlled repositories:
- `getStatus` (`app/src/lib/git/status.ts` line ~223) runs `git status --porcelain=2 -z` with `encoding: 'buffer'`, and the resulting buffer is fed to `parsePorcelainStatus` in `app/src/lib/status-parser.ts` with no size check before or after buffering. [2](#0-1) 
- `getCommits` (`app/src/lib/git/log.ts` lines 165-168) runs `git log` with `encoding: 'buffer'` and only trims individual `summary`/`body` fields to 100KB *after* the entire buffer has already been read into memory: [3](#0-2) 
- `getFilesDiffText` and other diff helpers in `app/src/lib/git/diff.ts` request `encoding: 'buffer'` and only check `stdout.length > 10 * 1024 * 1024` *after* the buffer has already been fully collected: [4](#0-3) 
- `getBlobContents` in `app/src/lib/git/show.ts` also uses unlimited `encoding: 'buffer'` (only `getPartialBlobContentsCatchPathNotInRef` explicitly bounds `maxBuffer` to a caller-supplied `length`): [5](#0-4) 

In every one of these cases the size check (if any) happens *after* the entire git output has already been buffered into the Node/Electron main process heap — the `Infinity` `maxBuffer` means there is no point at which the child process spawn/exec machinery itself will abort the read early, unlike the string path which is capped by dugite/Node at `kStringMaxLength`.

### Impact Explanation
A malicious or compromised git remote/repository (which the user clones or fetches, or a submodule/worktree contained within it) can be constructed to make any of these buffer-encoded git commands emit an extremely large amount of output — e.g., a repository with an enormous number of tracked/untracked files (`git status -z`), an enormous log/commit history or trailer content (`git log`), or a very large blob/diff. Since `maxBuffer` is `Infinity` for these calls, the Electron main process will attempt to buffer all of that output in memory before any post-hoc size check (like the 10MB diff check, or 100KB commit summary/body truncation) is applied. This can exhaust available memory in the main process, causing the entire Desktop application (not just a renderer tab) to crash or become unresponsive — an uncontrolled resource consumption/DoS directly analogous to the CryptoNote report's "attacker sends large list, node exhausts free memory before validating size."

### Likelihood Explanation
`getStatus` is called on essentially every repository refresh (background polling), so simply opening/using Desktop against a maliciously crafted repository (cloned by the user, or added as a remote/submodule) is enough to trigger the code path automatically, without any unusual user action. `getCommits`/diff-related buffer calls are triggered by routine UI interactions (viewing history, viewing diffs). No admin rights, local access, or prior malware are required — only that the user has added/cloned/fetched the attacker-controlled repository, which is within the stated valid-impact model ("attacker controls a cloned/fetched repository").

### Recommendation
Apply the same `kStringMaxLength`-class bound (or a purpose-specific, much smaller cap appropriate to each command, e.g. the existing `MaxDiffBufferSize`/10MB checks) to the `maxBuffer` option itself for buffer-encoded git calls, rather than only checking sizes after the fact. Concretely, in `app/src/lib/git/core.ts`, avoid the unconditional `Infinity` for `encoding === 'buffer'`; instead default to a bounded value (e.g. the largest size any buffer consumer is prepared to use, such as `MaxDiffBufferSize` for diff/show calls or a repo-status-specific ceiling), and have `getStatus`, `getCommits`, and diff/show functions pass an explicit `maxBuffer` and handle `isMaxBufferExceededError` gracefully (as `getPartialBlobContentsCatchPathNotInRef` already demonstrates is possible) instead of relying on post-buffering truncation.

### Proof of Concept
1. Create/clone a git repository where the working tree contains an extremely large number of distinct untracked file paths (or one whose log history has extremely large commit trailers/bodies, or whose blobs are extremely large).
2. Open this repository in GitHub Desktop or add it as a remote and fetch it.
3. Desktop's periodic background refresh invokes `getStatus` (`app/src/lib/git/status.ts`), which calls `git(['status', ...], ..., { encoding: 'buffer' })`; because `maxBuffer` resolves to `Infinity` in `app/src/lib/git/core.ts` line 234, the entire (huge) status output is buffered into the main process before `parsePorcelainStatus` runs, with no limit — repeated/large enough output drives up main-process memory until the application becomes unresponsive or crashes.

*Note: I could not execute this against a live Desktop instance to directly measure memory growth; the finding is based on static analysis of the `maxBuffer` configuration and its buffer-encoded call sites. A background Devin session with a runnable environment would be needed to empirically confirm the exact memory ceiling and crash threshold.*

### Citations

**File:** app/src/lib/git/core.ts (L231-235)
```typescript
  const defaultOptions: IGitExecutionOptions = {
    successExitCodes: new Set([0]),
    expectedErrors: new Set(),
    maxBuffer: options?.encoding === 'buffer' ? Infinity : kStringMaxLength,
  }
```

**File:** app/src/lib/git/status.ts (L221-224)
```typescript
  const { stdout, exitCode } = await git(args, repository.path, 'getStatus', {
    successExitCodes: new Set(rejectOnError ? [0] : [0, 128]),
    encoding: 'buffer',
  })
```

**File:** app/src/lib/git/log.ts (L165-193)
```typescript
  const result = await git(args, repository.path, 'getCommits', {
    successExitCodes: new Set([0, 128]),
    encoding: 'buffer',
  })

  // if the repository has an unborn HEAD, return an empty history of commits
  if (result.exitCode === 128) {
    return new Array<Commit>()
  }

  const parsed = parse(result.stdout)

  return parsed.map(commit => {
    // Ref is of the format: (HEAD -> master, tag: some-tag-name, tag: some-other-tag,with-a-comma, origin/master, origin/HEAD)
    // Refs are comma separated, but some like tags can also contain commas in the name, so we split on the pattern ", " and then
    // check each ref for the tag prefix. We used to use the regex /tag: ([^\s,]+)/g)`, but will clip a tag with a comma short.
    const tags = commit.refs
      .toString()
      .split(', ')
      .flatMap(ref => (ref.startsWith('tag: ') ? ref.substring(5) : []))

    return new Commit(
      commit.sha.toString(),
      commit.shortSha.toString(),
      commit.summary.subarray(0, 100 * 1024).toString(),
      commit.body.subarray(0, 100 * 1024).toString(),
      CommitIdentity.parseIdentity(commit.author.toString()),
      CommitIdentity.parseIdentity(commit.committer.toString()),
      commit.parents.length > 0 ? commit.parents.toString().split(' ') : [],
```

**File:** app/src/lib/git/diff.ts (L593-603)
```typescript
  const { stdout } = await git(args, repository.path, 'getFilesDiffText', {
    successExitCodes,
    encoding: 'buffer',
  })

  await unstageAll(repository)

  // No more than 10MB
  if (stdout.length > 10 * 1024 * 1024) {
    throw new Error('Diff is too large to render')
  }
```

**File:** app/src/lib/git/show.ts (L23-31)
```typescript
export const getBlobContents = (
  repository: Repository,
  commitish: string,
  path: string
) =>
  git(['show', `${commitish}:${path}`], repository.path, 'getBlobContents', {
    successExitCodes: new Set([0, 1]),
    encoding: 'buffer',
  }).then(r => r.stdout)
```
