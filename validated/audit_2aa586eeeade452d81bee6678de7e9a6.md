### Title
`getCommits` crashes on commits with a malformed/missing author or committer identity, breaking History for a maliciously crafted repository - ([File: app/src/lib/git/log.ts])

### Summary
This is the closest structural analog to the Penumbra pindexer bug: a single malformed timestamp field in an untrusted data source (a block, here a commit) is not defensively handled, and instead of substituting a default value the parser throws, taking down the entire batch-processing pipeline.

### Finding Description
`getCommits` in `app/src/lib/git/log.ts` runs `git log --date=raw` and, for every parsed commit record, calls `CommitIdentity.parseIdentity` unconditionally on the author and committer strings: [1](#0-0) 

`CommitIdentity.parseIdentity` in `app/src/models/commit-identity.ts` matches the identity string against a strict regex expecting `NAME <EMAIL> DIGITS(+/-)HHMM`, and throws a hard `Error` — not a recoverable/null result — if the regex fails to match or if the resulting date is `NaN`: [2](#0-1) 

Unlike `getAuthorIdentity` in `app/src/lib/git/var.ts`, which wraps the same `parseIdentity` call in a `try/catch` and safely returns `null` on failure: [3](#0-2) 

...the call inside `getCommits`'s `.map()` in `log.ts` has no such guard. Any single commit in the fetched/cloned history with an identity line that doesn't match the expected pattern (e.g. an empty/garbage author-date field, non-numeric date, or missing timezone offset) throws inside the `.map()` callback, propagating out of `getCommits` for the *entire* revision range being loaded — not just the one bad commit.

A commit object's author/committer lines are attacker-controlled data: Git only loosely validates the free-form ident string when a commit object is constructed directly (e.g. via `git commit-tree`/`hash-object` and pushed/packed into a repo), so a hostile repository can be crafted with such a commit and served to a victim via `git clone`/`git fetch`.

### Impact Explanation
`getCommits` backs core History/commit-list loading in `GitStore` (`app/src/lib/stores/git-store.ts`, which imports and calls `getCommits`), and other consumers like `getCommit`, `getAuthors`'s sibling `getAuthors`, and cherry-pick/squash/reorder/revert flows in `app/src/lib/git/*.ts`. If the promise rejects, callers that don't specifically wrap the call in error handling will propagate the failure up into the app's history-loading pipeline, causing History/commit list functionality for that repository to break (unhandled promise rejection / broken commit log rendering) purely from cloning or fetching a repository containing one such commit. This mirrors the pindexer crash class: a single malformed timestamp aborts processing of the whole batch rather than being defaulted and skipped.

### Likelihood Explanation
Constructing a commit with a non-conforming ident line requires only `git hash-object`/`git commit-tree` (or writing the commit object bytes directly) — no push access to a legitimate repo is needed, only that the victim clone or fetch the attacker's repository/branch. This fits the "attacker controls a cloned/fetched repository" impact category, since Desktop calls `getCommits` automatically as soon as a repository is opened/fetched into History view.

### Recommendation
Wrap the `CommitIdentity.parseIdentity` calls inside `getCommits`'s per-commit mapping in `app/src/lib/git/log.ts` in a `try/catch` (mirroring the pattern in `app/src/lib/git/var.ts`), defaulting to a safe placeholder identity (e.g. empty name/email and epoch date, similar in spirit to the Penumbra fix's "default timestamp for blocks without one") rather than throwing, and skip/log the malformed commit instead of failing the whole batch.

### Proof of Concept
1. In a scratch repo, create a commit whose committer/author ident does not match `NAME <EMAIL> DIGITS(+/-)HHMM`, e.g. via `GIT_AUTHOR_DATE=""` or a crafted commit object with `author Foo <foo@bar> not-a-timestamp +0000` written directly with `git hash-object -t commit -w` and updated via `git update-ref`.
2. Push/serve this repository (e.g. as a git remote) and have a victim `git clone`/fetch it in GitHub Desktop.
3. Open the repository in Desktop; `getCommits` is invoked when History loads, `CommitIdentity.parseIdentity` throws on the malformed record, and the exception propagates out of the `.map()` in `app/src/lib/git/log.ts:186-193`, causing the History view's commit-loading promise to reject for the whole batch rather than only the malicious commit. [4](#0-3) [5](#0-4)

### Citations

**File:** app/src/lib/git/log.ts (L175-204)
```typescript
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
      // We know for sure that the trailer separator will be ':' since we got
      // them from %(trailers:unfold) above, see `git help log`:
      //
      //   "key_value_separator=<SEP>: specify a separator inserted between
      //    trailer lines. When this option is not given each trailer key-value
      //    pair is separated by ": ". Otherwise it shares the same semantics as
      //    separator=<SEP> above."
      parseRawUnfoldedTrailers(commit.trailers.toString(), ':'),
      tags
    )
  })
```

**File:** app/src/models/commit-identity.ts (L10-36)
```typescript
  public static parseIdentity(identity: string): CommitIdentity {
    // See fmt_ident in ident.c:
    //  https://github.com/git/git/blob/3ef7618e6/ident.c#L346
    //
    // Format is "NAME <EMAIL> DATE"
    //  Markus Olsson <j.markus.olsson@gmail.com> 1475670580 +0200
    //
    // Note that `git var` will strip any < and > from the name and email, see:
    //  https://github.com/git/git/blob/3ef7618e6/ident.c#L396
    //
    // Note also that this expects a date formatted with the RAW option in git see:
    //  https://github.com/git/git/blob/35f6318d4/date.c#L191
    //
    const m = identity.match(/^(.*?) <(.*?)> (\d+) (\+|-)?(\d{2})(\d{2})/)
    if (!m) {
      throw new Error(`Couldn't parse identity ${identity}`)
    }

    const name = m[1]
    const email = m[2]
    // The date is specified as seconds from the epoch,
    // Date() expects milliseconds since the epoch.
    const date = new Date(parseInt(m[3], 10) * 1000)

    if (isNaN(date.valueOf())) {
      throw new Error(`Couldn't parse identity ${identity}, invalid date`)
    }
```

**File:** app/src/lib/git/var.ts (L37-41)
```typescript
  try {
    return CommitIdentity.parseIdentity(result.stdout)
  } catch (err) {
    return null
  }
```
