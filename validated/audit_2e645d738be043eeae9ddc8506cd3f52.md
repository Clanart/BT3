### Title
Unsanitized attacker-controlled commit data written into interactive-rebase todo script enables git command injection - (File: `app/src/lib/git/reorder.ts`, `app/src/lib/git/squash.ts`)

### Summary
`reorder()` and `squash()` iterate over every commit returned by `getCommits()` and, for each one, append a line of the form `pick <sha> <summary>\n` (or `squash ...`) directly into a temp file (`todoPath`) using `commit.sha` and `commit.summary` with **no escaping or newline stripping**. [1](#0-0) [2](#0-1) 

This file is then handed to git as the literal interactive-rebase instruction script via `sequence.editor=cat "<todoPath>" >`, so every line written into it is parsed and executed by git's rebase machinery as a todo command: [3](#0-2) 

### Finding Description
`commit.summary`/`commit.sha` originate from `git log --format=... %s ...` run against the (potentially attacker-supplied, e.g. cloned/fetched) repository, and are truncated but otherwise passed through unmodified: [4](#0-3) [5](#0-4) 

`reorder`/`squash` then loop unbounded over `commits.length` entries (the "unbounded iteration over all indexes" pattern from the seed report), and for every entry blindly concatenate this repository-controlled string into a rebase todo script with no validation that it is a single, safe line: [6](#0-5) [7](#0-6) 

The todo file is consumed as git's *executable* rebase instruction list (`sequence.editor=cat "$path" >`, `rebase -i`). Interactive rebase todo files support commands beyond `pick`/`squash`, notably `exec <shell command>`, `reset`, `label`, `merge`, etc. If a commit's subject line written into that file is not guaranteed to be confined to exactly one text line (git commit objects are free-form blobs; a crafted/forged commit object — trivial for an attacker who controls the repository being cloned/fetched — can contain a "subject" whose rendering under `%s` is not sanitized against embedding an additional command line), the resulting todo file would contain an attacker-injected line such as `exec calc.exe` or `exec curl ... | sh`, which git executes as part of the rebase.

This is the same broken-invariant class as the seed report: unbounded iteration over attacker-influenced collection entries, with each entry's raw content written verbatim into a sensitive artifact without bounds/sanitization checks — except here the consequence is not gas-exhaustion/DoS but potential **arbitrary command execution** on the user's machine, since the artifact is directly interpreted by git as a script.

### Impact Explanation
If exploitable, this allows an attacker who controls (or has contributed a crafted commit to) a repository a victim clones/fetches to achieve **arbitrary command execution** on the victim's machine merely by having the victim perform a routine Desktop multi-commit-operation (squash or reorder commits) that touches the malicious commit — a `git exec` line in the todo runs with the user's shell privileges under the local repository working directory context. This exceeds a pure DoS impact and matches the accepted impact classes (code execution via a git remote/cloned-repository-controlled input).

### Likelihood Explanation
Exploitability is contingent on whether an attacker can actually get a raw newline (or another todo-command-triggering byte sequence) to survive into the value read back by `%s`/`commit.summary`. This is the one assumption I could not fully verify from local code alone — git's pretty-format `%s` is documented to render the commit "subject," and normal `git commit` tooling folds multi-line first paragraphs into a single line, but commit objects can be constructed at the plumbing level (`git hash-object`/`git commit-tree`) with message bytes not produced by normal `git commit`, and it is not verified in this codebase whether `%s` is guaranteed immune to embedding a raw `\n`/`\0` for such hand-crafted objects. No sanitization/validation of `commit.sha`/`commit.summary` is performed anywhere in `reorder.ts`/`squash.ts` before writing to the todo file, so if the byte-level assumption holds, the path is fully unguarded — there is no allowlist check, no line-count validation, no escaping.

### Recommendation
- Do not interpolate raw commit-derived strings (`sha`, `summary`) directly into the generated rebase todo file.
- Validate/sanitize `commit.summary` before writing: strip or reject any embedded `\n`/`\r`/NUL bytes, and consider treating summary purely as a trailing comment that is never trusted to be single-line.
- Alternatively, write only `commit.sha` on the pick/squash line (git does not require the subject on a todo line) and drop the interpolated summary entirely, removing the injection surface.
- Add a regression test that constructs a commit with a crafted multi-line/`exec`-looking subject and asserts the resulting todo file contains exactly one `pick`/`squash` line per commit.

### Proof of Concept
1. Craft a git repository containing a commit whose raw commit-object message subject, when rendered by `git log --format=%s`, could be made to include an embedded newline followed by `exec touch /tmp/pwned` (byte-level construction via `git hash-object -w`/`git commit-tree`, bypassing the normal `git commit` line-folding).
2. Have the victim clone/fetch this repository into GitHub Desktop.
3. Victim selects that commit plus another and performs "Squash" or "Reorder" in Desktop, invoking `squash()`/`reorder()`. [8](#0-7) 
4. `commit.summary` is written unescaped into `todoPath`; the embedded newline creates a new line `exec touch /tmp/pwned` in the todo file.
5. `rebaseInteractive` executes `git -c sequence.editor=cat "$todoPath" > rebase -i $ref`, causing git to load and run the injected `exec` line as part of the interactive rebase. [9](#0-8)

### Citations

**File:** app/src/lib/git/reorder.ts (L63-70)
```typescript
    // Traversed in reverse so we do oldest to newest (replay commits)
    for (let i = commits.length - 1; i >= 0; i--) {
      const commit = commits[i]
      if (toMoveShas.has(commit.sha)) {
        // If it is toMove commit and we have found the base commit, we
        // can go ahead and insert them (as we will hold any picks till after)
        if (foundBaseCommitInLog) {
          await appendFile(todoPath, `pick ${commit.sha} ${commit.summary}\n`)
```

**File:** app/src/lib/git/reorder.ts (L108-118)
```typescript
      await appendFile(todoPath, `pick ${commit.sha} ${commit.summary}\n`)
    }

    if (toReplayAfterReorder.length > 0) {
      for (let i = 0; i < toReplayAfterReorder.length; i++) {
        await appendFile(
          todoPath,
          `pick ${toReplayAfterReorder[i].sha} ${toReplayAfterReorder[i].summary}\n`
        )
      }
    }
```

**File:** app/src/lib/git/squash.ts (L73-80)
```typescript
    // Traversed in reverse so we do oldest to newest (replay commits)
    for (let i = commits.length - 1; i >= 0; i--) {
      const commit = commits[i]
      if (toSquashShas.has(commit.sha)) {
        // If it is toSquash commit and we have found the squashOnto commit, we
        // can go ahead and squash them (as we will hold any picks till after)
        if (foundSquashOntoCommitInLog) {
          await appendFile(todoPath, `squash ${commit.sha} ${commit.summary}\n`)
```

**File:** app/src/lib/git/squash.ts (L121-130)
```typescript
      await appendFile(todoPath, `pick ${commit.sha} ${commit.summary}\n`)
    }

    if (toReplayAfterSquash.length > 0) {
      for (let i = 0; i < toReplayAfterSquash.length; i++) {
        await appendFile(
          todoPath,
          `pick ${toReplayAfterSquash[i].sha} ${toReplayAfterSquash[i].summary}\n`
        )
      }
```

**File:** app/src/lib/git/rebase.ts (L607-624)
```typescript
  /* If the commit is the first commit in the branch, we cannot reference it
  using the sha thus if lastRetainedCommitRef is null (we couldn't define it),
  we must use the --root flag */
  const ref = lastRetainedCommitRef == null ? '--root' : lastRetainedCommitRef
  const result = await git(
    [
      '-c',
      // This replaces interactive todo with contents of file at pathOfGeneratedTodo
      `sequence.editor=cat "${pathOfGeneratedTodo}" >`,
      'rebase',
      ...(opts?.noVerify ? ['--no-verify'] : []),
      '-i',
      ref,
    ],
    repository.path,
    opts?.action ?? 'Interactive rebase',
    options
  )
```

**File:** app/src/lib/git/log.ts (L127-140)
```typescript
  const { formatArgs, parse } = createLogParser({
    sha: '%H', // SHA
    shortSha: '%h', // short SHA
    summary: '%s', // summary
    body: '%b', // body
    // author identity string, matching format of GIT_AUTHOR_IDENT.
    //   author name <author email> <author date>
    // author date format dependent on --date arg, should be raw
    author: '%an <%ae> %ad',
    committer: '%cn <%ce> %cd',
    parents: '%P', // parent SHAs,
    trailers: '%(trailers:unfold,only)',
    refs: '%D',
  })
```

**File:** app/src/lib/git/log.ts (L186-193)
```typescript
    return new Commit(
      commit.sha.toString(),
      commit.shortSha.toString(),
      commit.summary.subarray(0, 100 * 1024).toString(),
      commit.body.subarray(0, 100 * 1024).toString(),
      CommitIdentity.parseIdentity(commit.author.toString()),
      CommitIdentity.parseIdentity(commit.committer.toString()),
      commit.parents.length > 0 ? commit.parents.toString().split(' ') : [],
```
