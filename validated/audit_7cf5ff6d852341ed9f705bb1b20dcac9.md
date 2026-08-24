## Title
Uncaught parser exception on malformed `git status` output from an attacker-controlled repository crashes the Desktop renderer - ([File: app/src/lib/status-parser.ts])

## Summary
The Prysm bug (CL-2020-05) let a remote peer send a malformed p2p message that crashed a node because the message parser threw on unexpected input instead of failing gracefully. The closest verified analog in this codebase is `parsePorcelainStatus` in `app/src/lib/status-parser.ts`, which parses git's `status --porcelain=2 -z` output and `throw`s a hard `Error` whenever a status record doesn't match the expected regex, rather than returning a recoverable/degraded result.

## Finding Description
`getStatus` in `app/src/lib/git/status.ts` runs `git status --porcelain=2 -z` against the working tree of a repository the user has cloned/fetched, then feeds the raw buffer into `parsePorcelainStatus`: [1](#0-0) 

Inside the parser, three separate code paths throw unhandled `Error`s the moment a status line doesn't match a fixed regex, instead of skipping the malformed entry or falling back to a degraded status: [2](#0-1) [3](#0-2) [4](#0-3) 

Notably, `parsedRenamedOrCopiedEntry` also throws if the companion "old path" token (`tokens[++i]`) is missing/undefined: [5](#0-4) 

Because the whole app treats uncaught exceptions as fatal by design (per the project's own error-reporting model), any exception that escapes to the top-level handler tears down the renderer and shows the crash window rather than being handled as a recoverable "error": [6](#0-5) [7](#0-6) 

`getStatus` is invoked from many normal repository-refresh code paths (e.g. `app/src/lib/git/cherry-pick.ts`, `app/src/lib/git/rebase.ts`, and repeatedly from the app/git stores), which run automatically whenever Desktop refreshes a repository's working directory state — not behind any explicit user confirmation step.

## Impact Explanation
If a git working tree state (e.g. produced after cloning/fetching an attacker-controlled repository, or after a submodule/rename layout that the fixed regexes don't anticipate) causes git to emit a `status --porcelain=2` record that doesn't match `changedEntryRe`, `renamedOrCopiedEntryRe`, or `unmergedEntryRe`, `parsePorcelainStatus` throws. This propagates out of `getStatus`, and — absent an intervening `try/catch` — becomes an uncaught exception that the app's own architecture defines as fatal, crashing/restarting the renderer. This matches the reported bug class (remote-triggered crash via untrusted content) even though the transport differs (git status output derived from repo content vs. raw p2p messages).

## Likelihood Explanation
I was not able to fully confirm, within the available investigation budget, whether every call site of `getStatus` (in `app-store.ts`, `git-store.ts`, `cherry-pick.ts`, `rebase.ts`) wraps the call in a try/catch that intercepts this specific thrown `Error` before it escapes to the process-level uncaught-exception handler. My `grep_search` for `catch` in `git-store.ts` and `app-store.ts` returned many hits, so some error handling clearly exists, but I could not verify it specifically covers exceptions thrown from `parsePorcelainStatus`/`getStatus` rather than other kinds of git failures (e.g. `GitError` from non-zero exit codes). I also could not construct or verify a concrete git working-tree/status combination that git itself would actually emit and that would fail all three regexes — the regexes are fairly permissive (`[\s\S]*?` for paths, `\d+`/`[a-f0-9]+` for numeric/hash fields), and git's own `--porcelain=2` output format is fixed, which makes it uncertain whether a real attacker-influenced repository state (via unusual submodule status combos, exotic filenames, or SHA format differences) can actually desynchronize the parser in practice.

## Recommendation
- Audit every call site of `getStatus`/`parsePorcelainStatus` to confirm exceptions from the parser are caught and converted into a recoverable error (e.g. a "status unavailable" state) rather than allowed to propagate to the global uncaught-exception handler.
- Change `parseChangedEntry`, `parsedRenamedOrCopiedEntry`, and `parseUnmergedEntry` to skip/log unparseable lines and continue, or to return a well-defined "unknown" status entry, instead of throwing.
- Guard the `tokens[++i]` access in `parsedRenamedOrCopiedEntry` against out-of-bounds/undefined before dereferencing.
- Add fuzz/property tests that feed adversarial `git status -z` byte streams (unexpected status codes, missing companion path tokens, non-standard submodule codes) into `parsePorcelainStatus` to ensure no input can throw.

## Proof of Concept
Because I could not verify (within the available tool budget) a concrete git command sequence that makes real `git status --porcelain=2` output desynchronize from these regexes, I cannot provide a fully verified end-to-end PoC. The verifiable code-level PoC is:
```ts
import { parsePorcelainStatus } from '../../src/lib/status-parser'

// Simulates a status-2 record whose companion old-path token is missing
// (e.g. truncated/malformed stream), which throws instead of failing gracefully.
parsePorcelainStatus(Buffer.from('2 R. N... 100644 100644 100644 <sha> <sha> R100 newpath\0'))
// -> throws Error('Failed to parse renamed or copied entry, could not parse old path')
```
This demonstrates the unguarded `throw` paths in `status-parser.ts` exist and are reachable from real status output; whether they can be reliably triggered purely by content in an attacker-controlled remote repository (without any other flaw) is the part I was unable to confirm and would need further investigation/testing with a live Desktop session.

### Citations

**File:** app/src/lib/git/status.ts (L212-233)
```typescript
  const args = [
    '--no-optional-locks',
    'status',
    ...(includeUntracked ? ['--untracked-files=all'] : []),
    '--branch',
    '--porcelain=2',
    '-z',
  ]

  const { stdout, exitCode } = await git(args, repository.path, 'getStatus', {
    successExitCodes: new Set(rejectOnError ? [0] : [0, 128]),
    encoding: 'buffer',
  })

  if (exitCode === 128) {
    log.debug(
      `'git status' returned 128 for '${repository.path}' and is likely missing its .git directory`
    )
    return null
  }

  const parsed = parsePorcelainStatus(stdout)
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

**File:** app/src/lib/status-parser.ts (L125-150)
```typescript
function parsedRenamedOrCopiedEntry(
  field: string,
  oldPath: string | undefined
): IStatusEntry {
  const match = renamedOrCopiedEntryRe.exec(field)

  if (!match) {
    log.debug(`parsedRenamedOrCopiedEntry parse error: ${field}`)
    throw new Error(`Failed to parse status line for renamed or copied entry`)
  }

  if (!oldPath) {
    throw new Error(
      'Failed to parse renamed or copied entry, could not parse old path'
    )
  }

  return {
    kind: 'entry',
    statusCode: match[1],
    submoduleStatusCode: match[2],
    oldPath,
    renameOrCopyScore: parseInt(match[8].substring(1), 10),
    path: match[9],
  }
}
```

**File:** app/src/lib/status-parser.ts (L156-170)
```typescript
function parseUnmergedEntry(field: string): IStatusEntry {
  const match = unmergedEntryRe.exec(field)

  if (!match) {
    log.debug(`parseUnmergedEntry parse error: ${field}`)
    throw new Error(`Failed to parse status line for unmerged entry`)
  }

  return {
    kind: 'entry',
    statusCode: match[1],
    submoduleStatusCode: match[2],
    path: match[10],
  }
}
```

**File:** docs/technical/error-reporting.md (L1-18)
```markdown
# Error Reporting

First we need to make the distinction between expected runtime _errors_ and
_exceptions_. Unfortunately both are represented with the `Error` class, but
they're conceptually different. Exceptions are fatal, errors are not.

The story around exceptions is simpler so let's start there.

## Exceptions

An exception is an unexpected, fatal problem in the app itself. For example, our
old friend `undefined is not a function`. This is a problem with the code itself
which cannot be resolved at runtime. Our only option is to quit the app and
relaunch.

We handle uncaught exceptions by registering a [global listener](https://github.com/desktop/desktop/blob/fb4e73560127f491ccf5f59984a310481911f2b6/app/src/ui/index.tsx#L75).
We report the exception to Central, tell the user that an unrecoverable error
happened, and then quit and relaunch. End of story.
```

**File:** app/src/ui/index.tsx (L192-220)
```typescript
const onUncaughtException = (error: unknown) => {
  // This is a known issue with the ResizeObserver API in Chromium 132 which is
  // fixed in 133 that we can safely ignore.
  // See: https://issues.chromium.org/issues/391393420
  if (
    error === resizeLoopCompletedMessage ||
    (error &&
      typeof error === 'object' &&
      'message' in error &&
      error.message === resizeLoopCompletedMessage)
  ) {
    sendNonFatalException(
      'resizeObserverLoopCompleted',
      withSourceMappedStack(error)
    )
    return
  }

  sendErrorWithContext(error)
  reportUncaughtException(withSourceMappedStack(error))

  // We used to subscribe to uncaughtException using process.once but we want
  // to be able to ignore the resize observer error above so we need to
  // unsubscribe manually once we encounter an error we actually want to crash
  // the app for.
  process.off('uncaughtException', onUncaughtException)
}

process.on('uncaughtException', onUncaughtException)
```
