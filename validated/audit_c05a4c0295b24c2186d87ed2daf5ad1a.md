### Title
Commit-message NUL-byte injection desynchronizes `createLogParser`, corrupting commit metadata parsed from a malicious repository - (File: `app/src/lib/git/git-delimiter-parser.ts`)

### Summary
`createLogParser` builds a `git log` format string by joining requested fields with the NUL byte (`%x00`) and then reconstructs individual commit records by splitting the raw output on `\0` and slicing it into fixed-size groups of `keys.length`. It never validates that the total number of `\0`-separated tokens is an exact multiple of `keys.length`, nor that any individual field (in particular `%b`, the commit body) is free of embedded NUL bytes. This mirrors the Nethermind root cause: a boundary/length invariant between an attacker-controlled payload and a fixed decoding scheme is assumed but never enforced, and downstream code indexes positionally without any guard.

### Finding Description
`app/src/lib/git/git-delimiter-parser.ts`: [1](#0-0) 

```
for (let i = 0; i < records.length - keys.length; i += keys.length) {
  const entry = {} as { [K in keyof T]: V }
  keys.forEach((key, ix) => (entry[key] = records[i + ix]))
  entries.push(entry)
}
```

This is consumed by `getCommits` in `app/src/lib/git/log.ts`, which asks Git for `sha`, `shortSha`, `summary`, `body`, `author`, `committer`, `parents`, `trailers`, and `refs`, joined with `%x00`: [2](#0-1) 

Git commit objects store the message as a length-prefixed byte blob, not a NUL-terminated C string, so an attacker who crafts a commit (e.g. via `git commit-tree`/`hash-object` with raw bytes) can embed a literal `0x00` byte inside the commit body (`%b`) or summary (`%s`). When the victim clones or fetches this repository and Desktop calls `getCommits`, `git log` prints that byte verbatim, adding an extra token to the NUL-delimited stream. Because `createLogParser.parse` performs pure positional slicing (`records[i + ix]`) with no equality check that each group actually corresponds to one commit's `keys.length` fields, this single injected NUL byte shifts the field alignment for that commit and **every subsequent commit in the parsed page** — sha, parents, trailers, author identity, and refs are silently reassigned to the wrong commit record. No exception is thrown; the corrupted objects are returned and mapped straight onto `Commit` model instances: [3](#0-2) 

The same parser factory (and the analogous `createForEachRefParser`, which has an equivalent unguarded modulo-based slicing loop) is reused by `stash.ts`, `diff.ts`, and `branch.ts`, so the same desync class applies to stash and ref listings.

### Impact Explanation
Downstream consumers trust the parsed fields without re-validation: `commit.parents`, `commit.trailers` (fed into `interpret-trailers.ts`, `format-commit-message.ts` and squash/amend message reconstruction), and `commit.sha` are used when Desktop builds new commit messages (e.g. squashing, rewording, cherry-picking, computing co-authors). A desynchronized trailer/author/parent set can therefore be silently substituted into a commit the user is about to create or amend — "silent corruption of what the user commits" as defined by the accepted impact set — without any error surfacing to the user. It can also mis-render commit history (wrong SHA/message pairing), which can mislead a user into pushing or acting on the wrong commit.

### Likelihood Explanation
No local/physical access, admin rights, or pre-existing malware is required: the only precondition is that the victim clones or fetches a repository controlled by an attacker (a normal, expected Desktop workflow). Crafting a commit with an embedded NUL byte in its message is straightforward with standard low-level Git plumbing and does not require the object to fail Git's own validation, since Git's internal buffers are 8-bit clean and not NUL-terminated. The vulnerable code path (`getCommits`) is exercised on every repository the user opens in Desktop.

### Recommendation
- Enforce a strict length-equality invariant in `createLogParser`/`createForEachRefParser`: verify `records.length % keys.length === 0` (for `createLogParser`) before slicing, and fail/skip malformed records instead of silently misaligning them.
- Prefer a self-describing framing that can't be perturbed by attacker-controlled field content, e.g. `-z`-record separation combined with per-record field counts, or escaping/rejecting any field value that itself contains the delimiter byte.
- Add a defensive check that rejects/sanitizes commit `%b`/`%s` values containing embedded `\0` before using them to build or reconstruct commit messages (squash/amend/cherry-pick), so any latent desync cannot flow into content the user subsequently commits or pushes.

### Proof of Concept
1. In an attacker-controlled repository, craft a commit whose message body contains a raw NUL byte, e.g. using low-level plumbing:
   ```
   printf 'tree <TREE_SHA>\nparent <PARENT_SHA>\nauthor A <a@a> 0 +0000\ncommitter A <a@a> 0 +0000\n\nnormal text\x00extra-token-after-nul' | git hash-object -t commit -w --stdin
   ```
   and update a ref (e.g. `refs/heads/main`) to point at the resulting commit SHA.
2. Victim clones/fetches this repository into GitHub Desktop.
3. Desktop calls `getCommits` (`app/src/lib/git/log.ts`), which runs `git log --format=%H%x00%h%x00%s%x00%b%x00...`. The injected `\0` inside the message body adds one extra token to the stream.
4. `createLogParser.parse` (`app/src/lib/git/git-delimiter-parser.ts`) slices the resulting token array in fixed `keys.length` chunks; from the malicious commit onward every subsequent commit's `sha`, `parents`, `trailers`, `author`, and `refs` fields are populated from the wrong offsets.
5. Observe in Desktop's history view (or via `format-commit-message.ts`/squash flows) that commit metadata for commits following the malicious one is mismatched (wrong SHA/author/trailers attached to the wrong commit), with no error reported to the user.

### Citations

**File:** app/src/lib/git/git-delimiter-parser.ts (L18-36)
```typescript
export function createLogParser<T extends Record<string, string>>(fields: T) {
  const keys: Array<keyof T> = Object.keys(fields)
  const format = Object.values(fields).join('%x00')
  const formatArgs = ['-z', `--format=${format}`]

  const parse = <V extends string | Buffer>(value: V) => {
    const records = (
      Buffer.isBuffer(value) ? splitBuffer(value, '\0') : value.split('\0')
    ) as V[]
    const entries = []

    for (let i = 0; i < records.length - keys.length; i += keys.length) {
      const entry = {} as { [K in keyof T]: V }
      keys.forEach((key, ix) => (entry[key] = records[i + ix]))
      entries.push(entry)
    }

    return entries
  }
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

**File:** app/src/lib/git/log.ts (L177-204)
```typescript
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
