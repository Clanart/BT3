## Verdict: Partially valid — different root cause than proposed, but the core invariant can still break

The exact **proof idea in the question (a trailer separator not covered by the default `':'` fallback) is not exploitable** for the code path that actually produces `Commit.trailers` used by `getSquashedCommitDescription`. However, a **related but different mismatch does exist** that can leak a residual, attacker-controlled trailer line into the squashed commit body.

### Why the proposed separator-mismatch vector is blocked

`Commit` objects consumed by the squash flow are built in `getCommits()`, which extracts trailers via git's own `%(trailers:unfold,only)` format specifier and then calls `parseRawUnfoldedTrailers` with a **hardcoded `':'`** separator — the code explicitly documents why this is safe: [1](#0-0) 

Since git itself performs the trailer extraction/unfolding (respecting `trailer.separators` config) and always renders the key/value pair with `": "` in its unfolded output, a trailer using a separator git doesn't recognize simply won't be extracted as a trailer at all — it won't be classified as `Co-Authored-By` by `isCoAuthoredByTrailer`, so it never enters `coAuthors` either. There's no separator "fallback mismatch" reachable through this path. [2](#0-1) 

### The actual mismatch: folded trailers vs. literal string reconstruction

`trimCoAuthorsTrailers` strips co-author trailers from the raw `body` by reconstructing the exact string `` `${token}: ${value}` `` and doing a literal (non-regex) `String.replace`: [3](#0-2) 

But `token`/`value` come from git's **unfolded** trailer output (`%(trailers:unfold,only)`), while `body` comes from the **raw, non-unfolded** commit body (`%b`): [4](#0-3) [5](#0-4) 

Git trailers support "folding": a trailer value can legally continue on subsequent lines with leading whitespace, and `%(trailers:unfold)` collapses that into a single line joined by a single space. If an attacker crafts a fetched commit whose `Co-Authored-By` trailer is folded, e.g.:

```
Co-Authored-By: Name
    <email@example.com>
```

then:
- The unfolded `value` git reports becomes `"Name <email@example.com>"` (single line, single space).
- The raw `body` still contains the original two-line, indented form.
- `trimCoAuthorsTrailers` searches for the literal substring `"Co-Authored-By: Name <email@example.com>"` in `body`, which never occurs verbatim (different whitespace/newline), so `String.replace` is a no-op.

The trailer line therefore survives untouched in `bodyNoCoAuthors`, and flows straight into `getSquashedCommitDescription`'s concatenation: [6](#0-5) 

### Impact

This is a real but narrow content-integrity bug: an attacker-controlled fetched commit with a folded co-author trailer can leave residual, unreviewed text in a squashed commit's body that the user then pushes — matching the "silent corruption of pushed commit content" impact class. It does **not** enable arbitrary text injection beyond what's already inside a legitimate trailer value, and it requires the specific folded-trailer formatting to trigger the whitespace mismatch (not any separator character trick).

### Recommendation

Replace the literal string reconstruction/removal in `trimCoAuthorsTrailers` with a line-oriented or regex-based removal that tolerates folded/whitespace variations (e.g., match `^token\s*:\s*value_first_line` and consume any indented continuation lines), or better, have git itself produce the body with trailers already stripped (e.g., via `git interpret-trailers` filtering) instead of doing manual string surgery in JS.

### Note on the submitted proof idea

The specific claim — "a trailer separator not covered by the default `:` fallback" leaking through — does not hold, because `getCommits()` sources trailers from git's own unfold/parse logic with a hardcoded `:` that's guaranteed correct by construction, per the inline comment in `app/src/lib/git/log.ts`. That exact proof-of-concept will not reproduce the described leak.

### Citations

**File:** app/src/lib/git/log.ts (L127-139)
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
```

**File:** app/src/lib/git/log.ts (L186-201)
```typescript
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
```

**File:** app/src/lib/git/interpret-trailers.ts (L19-21)
```typescript
export function isCoAuthoredByTrailer(trailer: ITrailer) {
  return trailer.token.toLowerCase() === 'co-authored-by'
}
```

**File:** app/src/models/commit.ts (L54-65)
```typescript
function trimCoAuthorsTrailers(
  trailers: ReadonlyArray<ITrailer>,
  body: string
) {
  let trimmedCoAuthors = body

  trailers.filter(isCoAuthoredByTrailer).forEach(({ token, value }) => {
    trimmedCoAuthors = trimmedCoAuthors.replace(`${token}: ${value}`, '')
  })

  return trimmedCoAuthors
}
```

**File:** app/src/lib/squash/squashed-commit-description.ts (L3-17)
```typescript
export function getSquashedCommitDescription(
  commits: ReadonlyArray<Commit>,
  squashOnto: Commit
): string {
  const commitMessages = commits.map(
    c => `${c.summary.trim()}\n\n${c.bodyNoCoAuthors.trim()}`
  )

  const descriptions = [
    squashOnto.bodyNoCoAuthors.trim(),
    ...commitMessages,
  ].filter(d => d.trim() !== '')

  return descriptions.join('\n\n')
}
```
