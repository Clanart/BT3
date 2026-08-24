## Finding: NUL-byte injection in commit message/body/trailers breaks field alignment in `getCommits`'s delimiter parser

### Summary

`getCommits` in [1](#0-0)  builds a `git log` invocation using a NUL-delimited format string (`%x00` between fields, `-z` as the record terminator) via `createLogParser`, then parses the raw buffer output with `git-delimiter-parser.ts`.

### Finding Description

The format string requests fields including `%s` (summary), `%b` (body), and `%(trailers:unfold,only)`: [2](#0-1) 

These are free-form text fields taken directly from the commit object's message, which — unlike filenames or ref names — Git does not validate for embedded NUL bytes when a commit object is constructed via low-level plumbing (e.g. `git hash-object` / `git commit-tree` with an arbitrary byte stream as the message). A malicious repository can therefore ship a commit whose message or trailers contain a literal `0x00` byte.

`createLogParser`'s `parse` function assumes each commit record contains **exactly** `keys.length` NUL-delimited fields and advances through the flat `records` array in fixed strides of `keys.length`: [3](#0-2) 

Because both the inter-field separator (`%x00`) and the git `-z` record terminator are the same NUL byte, an extra, attacker-injected NUL inside `%s`, `%b`, or the trailers field increases the number of NUL-split records for that commit beyond the expected count. Since `splitBuffer` (used when `encoding: 'buffer'`, as `getCommits` requests) simply splits on every NUL occurrence with no escaping mechanism: [4](#0-3) 

...the fixed-stride loop in `parse` becomes misaligned for that commit and for every subsequent commit in the same `git log` output, since there is no per-record boundary marker independent of field content — the loop just keeps consuming `keys.length` records at a time from a now-shifted array. This can cause:
- Fields from one commit (SHA, author, parents, refs, tags) to be silently attributed to a different commit.
- Commits to be dropped entirely from the parsed array (the loop bound `i < records.length - keys.length` can now terminate before consuming all real records, or two records can be fused into one malformed entry).
- Injected "commits" that don't correspond to any real commit, built from fragments of two adjacent real commits.

### Impact Explanation

`getCommits` output feeds the commit-history list, the "commits to be pushed" comparison view, and other UI surfaces the user relies on to know exactly what will be pushed/reviewed. If a cloned/fetched malicious repository can shift or drop entries here, a user could be shown an incorrect SHA/author/message for what they are about to push, or miss a commit that is actually included, silently misrepresenting what's about to be published — matching the "silent corruption of what the user commits or pushes" impact class.

### Likelihood Explanation

Requires the victim to clone/fetch a repository containing a maliciously crafted commit object with an embedded NUL byte in its message/trailers — fully within the "attacker controls a cloned/fetched repository content" threat model, no local access or credentials needed. Constructing such a commit requires plumbing commands, not `git commit` itself, but is a legitimate object that any Git implementation will read and pass through in `%b`/`%(trailers)`.

### Recommendation

Do not rely solely on git's `-z`/`%x00` NUL-based delimiting when the message/body/trailers fields are untrusted and can contain raw bytes. Options: reject/sanitize commit objects whose message contains embedded NUL bytes before display, or use a parsing strategy that doesn't conflate the record terminator and field separator (e.g., verify record boundaries independently, such as re-splitting only on the known fixed-width prefix fields like `%H`/`%h` which are guaranteed hex and NUL-free), and add bounds/consistency checks (e.g., verify `records.length` is an exact multiple of `keys.length`, and validate SHA fields look like SHAs) before trusting the parsed output.

### Proof of Concept

1. In a scratch repo, craft a commit object with an embedded NUL byte in the message using plumbing commands, e.g.:
   - Write a tree and get its SHA.
   - Build a raw commit object payload: `tree <sha>\nauthor A <a@a> 0 +0000\ncommitter A <a@a> 0 +0000\n\nSummary line\x00extra-hidden-field-injection`
   - `git hash-object -w -t commit --stdin < payload > commit_sha` and update a ref to point at it (`git update-ref refs/heads/main $(cat commit_sha)`), then commit a normal follow-up commit on top.
2. Have GitHub Desktop clone/fetch this repository and open the History view (which calls `getCommits`).
3. Assert: compare the number of entries `parse()` returns and their field contents against `git log -z --format=...` ground truth run manually — show the parsed array has a shifted/dropped/fused entry for the commit above the malicious one, demonstrating the parser output no longer exactly reflects the actual commit list.

### Citations

**File:** app/src/lib/git/log.ts (L120-168)
```typescript
export async function getCommits(
  repository: Repository,
  revisionRange?: string,
  limit?: number,
  skip?: number,
  additionalArgs: ReadonlyArray<string> = []
): Promise<ReadonlyArray<Commit>> {
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

  const args = ['log']

  if (revisionRange !== undefined) {
    args.push(revisionRange)
  }

  args.push('--date=raw')

  if (limit !== undefined) {
    args.push(`--max-count=${limit}`)
  }

  if (skip !== undefined) {
    args.push(`--skip=${skip}`)
  }

  args.push(
    ...formatArgs,
    '--no-show-signature',
    '--no-color',
    ...additionalArgs,
    '--'
  )
  const result = await git(args, repository.path, 'getCommits', {
    successExitCodes: new Set([0, 128]),
    encoding: 'buffer',
  })
```

**File:** app/src/lib/git/git-delimiter-parser.ts (L23-36)
```typescript
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

**File:** app/src/lib/split-buffer.ts (L1-14)
```typescript
export function splitBuffer(buffer: Buffer, delimiter: string): Buffer[] {
  const result = []
  let start = 0
  let index = buffer.indexOf(delimiter, start)
  while (index !== -1) {
    result.push(buffer.subarray(start, index))
    start = index + delimiter.length
    index = buffer.indexOf(delimiter, start)
  }
  if (start <= buffer.length) {
    result.push(buffer.subarray(start))
  }
  return result
}
```
