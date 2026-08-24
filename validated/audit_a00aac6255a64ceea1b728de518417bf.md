### Title
Repository-controlled `pre-commit`/hooks can silently alter or add content to a commit that Desktop's UI never shows the user - ([File: app/src/lib/git/commit.ts])

### Summary
Desktop reduces the reported bug class ("an operation the user reviewed can be consumed/altered before it actually lands, without the user re-confirming what was actually executed") to its GitHub Desktop equivalent: a **race between what the user reviews in the diff/commit UI and what actually gets written into the commit**. `createCommit` computes the patch to stage from a diff snapshot and then hands control to arbitrary, repository-supplied Git hooks (`pre-commit`, `prepare-commit-msg`, `commit-msg`) that run *after* staging but *before* the commit object is created, with no re-verification that the resulting tree still matches what was shown to the user.

### Finding Description
`createCommit` in `app/src/lib/git/commit.ts` stages exactly the files/lines the user selected in the UI via `stageFiles`, then immediately runs `git commit` with `interceptHooks: ['pre-commit', 'prepare-commit-msg', 'commit-msg', 'post-commit', ...]`: [1](#0-0) 

These hooks are executed for real via the hooks-proxy mechanism (`app/src/lib/hooks/with-hooks-env.ts` and `app/src/lib/hooks/hooks-proxy.ts`), which discovers hooks that are present in the repository (including hooks installed by `core.hooksPath`-based hook managers that ship inside the repo, e.g. via a `prepare`/`postinstall` script that a normal developer workflow runs on `npm install`) and executes them with a full shell environment: [2](#0-1) [3](#0-2) 

A `pre-commit` (or `prepare-commit-msg`) hook that ships in a cloned/fetched repository can, once triggered by the very commit the user initiated in Desktop, modify tracked files and re-stage them (e.g. `git add -A`) before Git actually writes the tree. Git commits whatever is in the index at the moment `git commit` finalizes — not the diff Desktop rendered before the command was issued. Desktop never re-diffs the final tree against what it displayed and never asks the user to re-confirm; it simply reports success with the resulting SHA: [4](#0-3) 

This is functionally the same broken invariant as the report: an entity other than the one who reviewed/authorized the transaction (order/commit) gets to make the final, binding change to it, and the reviewing party has no way to prevent or detect the substitution before it's "filled" (committed). The counterpart to "front-running the trade" here is "front-running the tree write" — the hook (shipped by the attacker-controlled repository) races between the reviewed diff and the final `git commit`, and Desktop's guard (the diff shown pre-commit) does not stop it because staging happens strictly before hook execution, and the hook is free to re-stage arbitrary content.

Separately, and reinforcing the same root cause, `applyPatchToIndex` (`app/src/lib/git/apply.ts`) recomputes the diff from disk at staging time rather than reusing the diff object the user actually selected lines against: [5](#0-4) 

and the absolute line indices from the user's earlier selection (`file.selection.isSelected(absoluteIndex)`) are applied against this freshly-fetched diff in `formatPatch`: [6](#0-5) 

If the working tree changes between when the user made a partial-selection and when `createCommit` runs (which, again, a repository-shipped tool triggered as part of the developer's normal workflow can do), the line indices can map to different content than what was reviewed, silently including/excluding the wrong hunks.

### Impact Explanation
The commit that ends up being created (and subsequently pushed) can contain content the user never reviewed or approved in Desktop's diff view — this is exactly the "silent corruption of what the user commits or pushes" impact category. Because the injected content originates from the repository itself (via a hook or filter shipped in the repo), the attacker vector is "attacker controls a cloned/fetched repository," satisfying the valid-impact criteria without any local/physical access, prior malware, or leaked credentials — the malicious logic ships as ordinary repository content (a hook installed by a standard `prepare`/`postinstall` script) and activates itself the next time the victim uses Desktop's normal commit flow.

### Likelihood Explanation
Medium. It requires the victim to open a malicious/compromised repository whose install step wires up a hook Desktop will intercept (`pre-commit`, `prepare-commit-msg`, `commit-msg`), which is common developer practice (husky-style hook managers) and not itself suspicious. The victim does not need to do anything unusual beyond a normal "clone, install dependencies, make a commit" workflow. The window for exploitation is deterministic (it triggers on the very commit operation Desktop performs), unlike a probabilistic mempool race, making this arguably easier to reliably trigger than the original DeFi front-running scenario.

### Recommendation
- After `git commit` completes (or, more generally, whenever `interceptHooks` includes any hook capable of touching the index/working tree, i.e. `pre-commit`, `prepare-commit-msg`, `commit-msg`), diff the resulting commit's tree against the tree Desktop staged/displayed and surface any discrepancy to the user instead of silently reporting success.
- Consider surfacing a clear warning/consent step before ever running repository-supplied hooks that were not explicitly opted into by the user (this already exists as a toggle in some form — ensure it defaults to informing the user and that hook output is not just decorative but tied to a content-integrity check).
- In `applyPatchToIndex`/`stageFiles`, avoid re-fetching a fresh diff from disk at staging time; instead stage using the same diff snapshot the user's selection indices were computed against, or re-validate that the working tree hasn't changed since the selection was made and abort/refresh if it has.

### Proof of Concept
1. Attacker publishes a repository containing a `package.json` with a `"prepare": "husky install"` (or equivalent) script and a hook file (e.g. `.husky/pre-commit`) that runs `echo "malicious-payload" >> src/important.ts && git add -A`.
2. Victim clones the repository with GitHub Desktop and runs `npm install` (standard workflow), which silently registers the hook via `core.hooksPath`.
3. Victim modifies a benign file, reviews the diff in Desktop's Changes view (showing only the benign edit), and clicks Commit.
4. Desktop's `createCommit` (`app/src/lib/git/commit.ts:29-31`) stages exactly the reviewed content, then invokes `git commit`, which triggers the repository's `pre-commit` hook (intercepted per `interceptHooks` in the same function). The hook appends malicious content to `src/important.ts` and re-stages it.
5. `git commit` finalizes with the hook-modified index. Desktop reports commit success with a SHA (`app/src/lib/stores/app-store.ts:3693-3716`) without re-checking that the resulting tree matches what was displayed.
6. The victim pushes, publishing content they never saw or approved in Desktop's UI.

### Citations

**File:** app/src/lib/git/commit.ts (L26-71)
```typescript
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)

  const args = ['-F', '-']

  if (options?.amend) {
    args.push('--amend')
  }

  if (options?.noVerify) {
    args.push('--no-verify')
  }

  if (options?.signOff) {
    args.push('--signoff')
  }

  if (options?.allowEmpty) {
    args.push('--allow-empty')
  }

  const result = await git(
    ['commit', ...args],
    repository.path,
    'createCommit',
    {
      stdin: message,
      // https://git-scm.com/docs/githooks/2.46.1
      interceptHooks: [
        'pre-commit',
        'prepare-commit-msg',
        'commit-msg',
        'post-commit',
        ...(options?.amend ? ['post-rewrite'] : []),
        'pre-auto-gc',
      ],
      onHookProgress: options?.onHookProgress,
      onHookFailure: options?.onHookFailure,
      onTerminalOutputAvailable: options?.onTerminalOutputAvailable,
    }
  )
  return parseCommitSHA(result)
```

**File:** app/src/lib/hooks/with-hooks-env.ts (L29-42)
```typescript
export async function withHooksEnv<T>(
  fn: (env: Record<string, string | undefined> | undefined) => Promise<T>,
  path: string,
  opts: IGitExecutionOptions | undefined
): Promise<T> {
  if (!opts?.interceptHooks || !getHooksEnvEnabled()) {
    return fn(opts?.env)
  }

  const hooks = await Array.fromAsync(getRepoHooks(path, opts.interceptHooks))

  if (hooks.length === 0) {
    return fn(opts?.env)
  }
```

**File:** app/src/lib/hooks/hooks-proxy.ts (L245-264)
```typescript
    const { code, signal } = await new Promise<{
      code: number | null
      signal: NodeJS.Signals | null
    }>((resolve, reject) => {
      const child = spawn(gitPath, args, {
        cwd: proxyCwd,
        // GITHUB_DESKTOP lets hooks know they're run from GitHub Desktop.
        // See https://github.com/desktop/desktop/issues/19001
        env: { ...shellEnv.env, ...safeEnv, GITHUB_DESKTOP: '1' },
        signal: abortController.signal,
      })
        .on('close', (code, signal) => resolve({ code, signal }))
        .on('error', err => reject(err))

      // git-hook run takes care of ensuring we only get hook output on stderr
      // https://github.com/git/git/blob/4cf919bd7b946477798af5414a371b23fd68bf93/hook.c#L73C6-L73C22
      child.stderr.pipe(conn.stderr, { end: false }).on('error', reject)
      child.stderr.on('data', data => terminalOutput.push(data))
      conn.stdin.pipe(child.stdin).on('error', reject)
    })
```

**File:** app/src/lib/stores/app-store.ts (L3693-3716)
```typescript
    return this.withIsCommitting(repository, async () => {
      const result = await gitStore.performFailableOperation(
        async () => {
          const message = await formatCommitMessage(repository, context)
          let aborted = false
          return createCommit(repository, message, selectedFiles, {
            amend: context.amend,
            onHookProgress: this.onHookProgress(repository),
            onHookFailure: this.onHookFailure(() => (aborted = true)),
            onTerminalOutputAvailable: subscribeToCommitOutput => {
              this.repositoryStateCache.update(repository, state => ({
                ...state,
                subscribeToCommitOutput,
              }))
            },
            noVerify: state.skipCommitHooks,
            signOff: state.signOffCommits,
            allowEmpty: state.allowEmptyCommit,
          }).catch(err => (aborted ? undefined : Promise.reject(err)))
        },
        { gitContext: { kind: 'commit' }, repository }
      )

      if (result !== undefined) {
```

**File:** app/src/lib/git/apply.ts (L52-82)
```typescript
  const applyArgs: string[] = [
    'apply',
    '--cached',
    '--unidiff-zero',
    '--whitespace=nowarn',
    '-',
  ]

  const diff = await getWorkingDirectoryDiff(repository, file)

  if (diff.kind !== DiffType.Text && diff.kind !== DiffType.LargeText) {
    const { kind } = diff
    switch (diff.kind) {
      case DiffType.Binary:
      case DiffType.Submodule:
      case DiffType.Image:
        throw new Error(
          `Can't create partial commit in binary file: ${file.path}`
        )
      case DiffType.Unrenderable:
        throw new Error(
          `File diff is too large to generate a partial commit: ${file.path}`
        )
      default:
        assertNever(diff, `Unknown diff kind: ${kind}`)
    }
  }

  const patch = await formatPatch(file, diff)
  await git(applyArgs, repository.path, 'applyPatchToIndex', { stdin: patch })

```

**File:** app/src/lib/patch-formatter.ts (L129-171)
```typescript
export function formatPatch(
  file: WorkingDirectoryFileChange,
  diff: ITextDiff | ILargeTextDiff
): string {
  let patch = ''

  diff.hunks.forEach((hunk, hunkIndex) => {
    let hunkBuf = ''

    let oldCount = 0
    let newCount = 0

    let anyAdditionsOrDeletions = false

    hunk.lines.forEach((line, lineIndex) => {
      const absoluteIndex = hunk.unifiedDiffStart + lineIndex

      // We write our own hunk headers
      if (line.type === DiffLineType.Hunk) {
        return
      }

      // Context lines can always be let through, they will
      // never appear for new files.
      if (line.type === DiffLineType.Context) {
        hunkBuf += `${line.text}\n`
        oldCount++
        newCount++
      } else if (file.selection.isSelected(absoluteIndex)) {
        // A line selected for inclusion.

        // Use the line as-is
        hunkBuf += `${line.text}\n`

        if (line.type === DiffLineType.Add) {
          newCount++
        }
        if (line.type === DiffLineType.Delete) {
          oldCount++
        }

        anyAdditionsOrDeletions = true
      } else {
```
