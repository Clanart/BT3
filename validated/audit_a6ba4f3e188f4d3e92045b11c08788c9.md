## Analysis

`git`'s `--` argument terminator only tells the CLI parser "stop treating following tokens as options" — it does **not** disable *pathspec magic* parsing. Pathspec magic signatures (`:(glob)`, `:(icase)`, `:(top)`, `:(exclude)`, etc.) are parsed by the pathspec layer regardless of `--`, and are only suppressed by `--literal-pathspecs`, the `GIT_LITERAL_PATHSPECS=1` environment variable, or an explicit `:(literal)` prefix on the pathspec itself.

`removeConflictedFile` passes the raw status `path` straight through with none of these protections: [1](#0-0) 

Desktop's `git()` wrapper (`app/src/lib/git/core.ts`) sets `TERM`, credential-helper and trampoline env vars but never `GIT_LITERAL_PATHSPECS`: [2](#0-1) 

The codebase is in fact aware of this exact class of bug — `diff.ts` defines `ensureRelativePath`, which prefixes absolute paths with the `:(top,literal)` magic to force literal interpretation — but this mitigation is applied only in `diff.ts`, not in `rm.ts`, `add.ts`, or `checkout.ts`'s conflicted-file helpers: [3](#0-2) [4](#0-3) [5](#0-4) 

The `path` value itself comes unsanitized straight from `git status --porcelain=2 -z` parsing (unmerged/conflict entries via `parseUnmergedEntry`), and the regex used places no restriction on the character set of the captured filename, so a path beginning with `:(` is accepted verbatim: [6](#0-5) 

Git itself only forbids `NUL` and `/` in a path component — colons, parentheses, and asterisks are all legal on POSIX filesystems (ext4, APFS) — so a real repository can contain a tracked/conflicted file whose literal name is e.g. `:(glob)*`.

## Title
`git rm --` in `removeConflictedFile` fails to disable pathspec magic, allowing an attacker-crafted conflicted filename to expand file deletion beyond the single file shown - (File: app/src/lib/git/rm.ts)

## Summary
`removeConflictedFile` executes `git(['rm', '--', file.path], ...)` using the raw `path` string reported by `git status --porcelain=2 -z`. Because `--` does not disable Git's pathspec "magic" syntax (`:(glob)`, `:(icase)`, etc.), a conflicted file whose real name begins with a magic token (e.g. `:(glob)*`) is not treated as a literal filename by `git rm`. Instead, Git interprets the token as pathspec magic and the remainder as a matching pattern, potentially matching and deleting many tracked files instead of the single conflicted file the Changes list displayed to the user.

## Finding Description
1. An attacker crafts/pushes a repository containing a file whose literal on-disk name is a Git pathspec magic signature followed by a wildcard, e.g. `:(glob)*`, such that it ends up in a merge-conflict state (`DU`/`UD`/`AA`/etc.).
2. `parsePorcelainStatus`/`parseUnmergedEntry` in `app/src/lib/status-parser.ts` captures this string verbatim into `IStatusEntry.path` with no validation of a leading `:(` sequence.
3. Desktop's Changes/Conflicts UI shows exactly one conflicted entry named `:(glob)*`.
4. When the user chooses to resolve the conflict by deleting that one file, `removeConflictedFile` is invoked and runs `git rm -- :(glob)*`.
5. Because `--` only stops option parsing (not pathspec-magic parsing), and Desktop never sets `GIT_LITERAL_PATHSPECS`/`--literal-pathspecs`/`:(literal)`, Git parses `:(glob)` as a magic signature and `*` as a glob pattern applied against the whole tree, matching and removing every file that satisfies the pattern from both the index and working tree — not just the single file the user clicked.

This is the same class of bug that the codebase's own `ensureRelativePath` helper in `diff.ts` was written to guard against, but that guard was never applied to `rm.ts` (nor to the structurally identical `add.ts:addConflictedFile` and `checkout.ts:checkoutConflictedFile`).

## Impact Explanation
A malicious repository can cause Desktop to silently delete working-tree and index content far beyond what the UI represented to the user as "one conflicted file," when the user performs the ordinary, expected action of resolving a conflict via delete. This is silent corruption of what the user subsequently commits/pushes (deleted files removed from the index), satisfying the program's impact criteria for attacker-controlled repository content leading to unintended repository state changes.

## Likelihood Explanation
Requires only that the victim clone/fetch an attacker-controlled repository containing a merge conflict where one side introduces a file with a pathspec-magic-shaped name, and then resolve that conflict by choosing "Deleted" in Desktop's normal conflict-resolution UI — a natural, expected user action, not an unusual or contrived step.

## Recommendation
Apply the same literal-pathspec protection already used in `diff.ts` (`ensureRelativePath`) to every git invocation that passes a status-derived `path` as a pathspec, including `removeConflictedFile` (`app/src/lib/git/rm.ts`), `addConflictedFile` (`app/src/lib/git/add.ts`), and `checkoutConflictedFile`/`checkoutPaths` (`app/src/lib/git/checkout.ts`). Simplest fix: prefix all such paths with `:(literal)` unconditionally (not just for absolute paths), or set `GIT_LITERAL_PATHSPECS=1` in the environment used by Desktop's `git()`/`spawnGit()` wrappers so pathspec magic is globally disabled for all Desktop-issued commands.

## Proof of Concept
1. Prepare a repo where a merge produces a conflict on a file literally named `:(glob)*` (filesystem/Git both permit `:`, `(`, `)`, `*` in filenames).
2. Open the repository in Desktop; the Changes/Conflicts panel shows a single conflicted entry `:(glob)*`.
3. Click "Resolve as Deleted" for that entry, triggering `removeConflictedFile(repository, file)` → `git(['rm', '--', ':(glob)*'], repo.path, 'removeConflictedFile')`.
4. Observe (via `git status`/terminal output) that Git removed multiple tracked files matching the glob pattern from the index and working tree, not just the single `:(glob)*` entry shown in the UI — diverging from the user's intended single-file deletion.

### Citations

**File:** app/src/lib/git/rm.ts (L26-31)
```typescript
export async function removeConflictedFile(
  repository: Repository,
  file: WorkingDirectoryFileChange
) {
  await git(['rm', '--', file.path], repository.path, 'removeConflictedFile')
}
```

**File:** app/src/lib/git/core.ts (L276-295)
```typescript
  return withHooksEnv(
    hooksEnv =>
      withTrampolineEnv(
        async env => {
          const commandName = `${name}: git ${args.join(' ')}`

          const result = await GitPerf.measure(commandName, () =>
            exec(args, path, {
              ...opts,
              env: {
                // Explicitly set TERM to 'dumb' so that if Desktop was launched
                // from a terminal or if the system environment variables
                // have TERM set Git won't consider us as a smart terminal.
                // See https://github.com/git/git/blob/a7312d1a2/editor.c#L11-L15
                TERM: 'dumb',
                ...opts.env,
                ...hooksEnv,
                ...env,
              },
            })
```

**File:** app/src/lib/git/diff.ts (L999-1004)
```typescript
// Prefix absolute path with `:(top,literal)` to ensure that git treats it as a
// literal path. This is important for paths that appear to be absolute paths on
// some platforms and not others. See
// https://git-scm.com/docs/gitglossary#Documentation/gitglossary.txt-top
const ensureRelativePath = (path: string) =>
  isAbsolute(path) ? `:(top,literal)${path}` : path
```

**File:** app/src/lib/git/add.ts (L11-15)
```typescript
export async function addConflictedFile(
  repository: Repository,
  file: WorkingDirectoryFileChange
) {
  await git(['add', '--', file.path], repository.path, 'addConflictedFile')
```

**File:** app/src/lib/git/checkout.ts (L225-234)
```typescript
export async function checkoutConflictedFile(
  repository: Repository,
  file: WorkingDirectoryFileChange,
  resolution: ManualConflictResolution
) {
  await git(
    ['checkout', `--${resolution}`, '--', file.path],
    repository.path,
    'checkoutConflictedFile'
  )
```

**File:** app/src/lib/status-parser.ts (L152-170)
```typescript
// u <xy> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>
const unmergedEntryRe =
  /^u ([DAU]{2}) (N\.\.\.|S[C.][M.][U.]) (\d+) (\d+) (\d+) (\d+) ([a-f0-9]+) ([a-f0-9]+) ([a-f0-9]+) ([\s\S]*?)$/

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
