### Title
Byte-boundary truncation of attacker-controlled commit summary/body corrupts commit messages reused for amend/undo - ([File: app/src/lib/git/log.ts])

### Summary
`getCommits` reads `git log` output as a raw `Buffer` and truncates the commit `summary` and `body` fields to a fixed byte length (`100 * 1024`) via `Buffer.subarray()` before calling `.toString()`, with no boundary check to ensure the cut doesn't land in the middle of a multi-byte UTF-8 sequence, and no indication to the caller that truncation occurred. [1](#0-0) 

### Finding Description
`getCommits` invokes `git log` with `encoding: 'buffer'` and parses the raw output with `createLogParser`, then builds each `Commit` model directly from the parsed buffers. [2](#0-1) 

The summary and body fields are hard-truncated at a fixed byte offset before being decoded to a string:
```
commit.summary.subarray(0, 100 * 1024).toString(),
commit.body.subarray(0, 100 * 1024).toString(),
``` [3](#0-2) 

This mirrors the structural flaw in the external report: a fixed-width slot (here, a fixed byte cap) is filled from attacker-controlled data without checking whether the value being packed fits cleanly within that width. Just as `LibLastReserveBytes::storeLastReserves` silently discards the high-order bytes of an oversized reserve when it doesn't fit in `bytes16`, this code silently discards everything past 100KB of a commit message and — because the cut is done on raw bytes rather than at a UTF-8 character boundary — can also split a multi-byte character, producing an invalid/garbled trailing character via `Buffer.toString()` (which replaces incomplete sequences with U+FFFD). Neither case validates the size/alignment of the value before it is packed into the fixed container, and neither surfaces any warning that data was cut.

A commit with an oversized or maliciously crafted message is entirely under the control of whoever authored the commit in a repository the user clones, fetches, or checks out (e.g., a malicious PR branch or upstream commit) — this satisfies the "attacker controls a cloned/fetched repository" precondition.

The truncated, potentially corrupted `summary`/`body` values then propagate into commit-editing flows that reuse a prior commit's message as the basis for a new one:
- `undoCommit` copies `commit.summary`/`commit.body` verbatim into the active commit message state. [4](#0-3) 
- `prepareToAmendCommit` does the same when the user chooses to amend a commit. [5](#0-4) 
- `loadCommitAndCoAuthors` builds a full commit message from the same fields for trailer re-parsing, then reuses them if trailer extraction fails. [6](#0-5) 

If the user commits/amends without noticing the truncation deep inside a 100KB message, the resulting new commit that gets created (and potentially pushed) silently contains a corrupted/incomplete message rather than what the original author (or the user) intended.

### Impact Explanation
This does not grant code execution or credential exfiltration, but it does match the "silent corruption of what the user commits or pushes" impact category: the content of a newly created commit (via amend/undo-then-recommit flows) can be silently altered relative to the source commit, with a hard cutoff that can also introduce garbled bytes at the truncation point, and with no user-facing indication that truncation happened.

### Likelihood Explanation
Exploitability requires only that the user open a repository containing a commit with an oversized message (achievable by anyone able to push/PR to a repo the victim later clones/fetches) and later choose to amend or undo that specific commit — no special local access, admin rights, or social engineering beyond normal Desktop usage (browsing a cloned repo's history and using the built-in "Amend" / "Undo commit" feature) is required. Likelihood is limited by the somewhat unusual precondition of a >100KB commit message, and by the fact the (truncated) text is visible in the commit message editor before the user submits, which could allow attentive users to notice something is off — though a 100KB text area is not something users typically read end-to-end before committing.

### Recommendation
- When truncating `commit.summary`/`commit.body`, ensure the cut point is aligned to a valid UTF-8 character boundary (e.g., decode incrementally or use a UTF-8-safe truncation helper) rather than slicing raw bytes.
- Surface truncation to the caller/UI (e.g., an `isTruncated` flag on `Commit`) so `git-store.ts` can warn the user before reusing a truncated message as the basis for amend/undo, instead of silently substituting incomplete content.

### Proof of Concept
1. In a test repository, create a commit whose body exceeds 100KB and includes a multi-byte UTF-8 character (e.g., an emoji) straddling the 102400-byte offset.
2. Run `getCommits` (or open the repository's history in Desktop) and inspect the resulting `Commit.body` — it will contain a mangled character (`\uFFFD`) at the truncation boundary, and any content past byte 102400 will be silently missing.
3. In Desktop, select that commit and choose "Amend commit" or "Undo commit" — the commit message editor is pre-populated from `commit.summary`/`commit.body` via `prepareToAmendCommit`/`undoCommit`. [5](#0-4) 
4. Committing from this state produces a new commit whose message differs from the original beyond the point the user intended, with no warning that truncation/corruption occurred.

### Citations

**File:** app/src/lib/git/log.ts (L165-176)
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

**File:** app/src/lib/stores/git-store.ts (L736-742)
```typescript
    this._commitMessage = {
      summary: commit.summary,
      description: commit.body,
      timestamp: Date.now(),
    }
    this.emitUpdate()
  }
```

**File:** app/src/lib/stores/git-store.ts (L744-756)
```typescript
  public async prepareToAmendCommit(commit: Commit) {
    const coAuthorsRestored = await this.restoreCoAuthorsFromCommit(commit)
    if (coAuthorsRestored) {
      return
    }

    this._commitMessage = {
      summary: commit.summary,
      description: commit.body,
      timestamp: Date.now(),
    }
    this.emitUpdate()
  }
```

**File:** app/src/lib/stores/git-store.ts (L786-810)
```typescript
  private async loadCommitAndCoAuthors(commit: Commit) {
    const repository = this.repository

    // git-interpret-trailers is really only made for working
    // with full commit messages so let's start with that
    const message = await formatCommitMessage(repository, {
      summary: commit.summary,
      description: commit.body,
    })

    // Next we extract any co-authored-by trailers we
    // can find. We use interpret-trailers for this
    const foundTrailers = await parseTrailers(repository, message)
    const coAuthorTrailers = foundTrailers.filter(isCoAuthoredByTrailer)

    // This is the happy path, nothing more for us to do
    if (coAuthorTrailers.length === 0) {
      this._commitMessage = {
        summary: commit.summary,
        description: commit.body,
        timestamp: Date.now(),
      }

      return
    }
```
