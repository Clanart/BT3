## Analysis

The Linea report's broken invariant is: **a decoder assumes a fixed record shape and blindly assigns bytes to fields without verifying that the actual byte stream matches that shape**, so attacker-controlled extra/embedded data silently shifts field boundaries and two different inputs decode to the same (or contextually wrong) output.

The closest structural analog in GitHub Desktop is the NUL-delimited record parser used to decode `git log --format=... -z` output.### Title
Fixed-stride NUL-delimited git log record parser misaligns commit fields on embedded NUL bytes — (File: app/src/lib/git/git-delimiter-parser.ts)

### Summary
`createLogParser` decodes `git log --format=... -z` output by splitting the entire byte stream on `\0` and then slicing it into fixed-size chunks (`keys.length` records per commit) without verifying that each individual field actually contains no additional NUL bytes. This is structurally the same flaw as the `SparseMerkleProof._parseAccount` bug: a decoder trusts a fixed-shape/fixed-length assumption instead of validating the actual boundaries of the data it is decoding, so attacker-controlled content (a commit body/message containing a raw `0x00` byte) can shift field boundaries and cause a different, misleading decode result with no error raised.

### Finding Description
`createLogParser` builds the format string by joining requested fields with the NUL separator `%x00`, and combines this with `-z` (which itself terminates each commit record with NUL): [1](#0-0) 

The parser then does a naive fixed-stride reconstruction: [2](#0-1) 

`splitBuffer`/`String.split('\0')` treats *every* `0x00` byte in the buffer as a field boundary, with no distinction between "structural" separators inserted by `%x00`/`-z` and any NUL byte that happens to be part of the actual field content (e.g., a commit's free-form message body, `%b`): [3](#0-2) 

`getCommits` invokes this parser with 9 fields per commit — `sha`, `shortSha`, `summary`, `body`, `author`, `committer`, `parents`, `trailers`, `refs` — and the loop advances by a fixed stride of `keys.length` (9) per iteration with no re-synchronization or length verification: [4](#0-3) [5](#0-4) 

Git's commit objects are raw byte buffers (`commit <size>\0<content>`), and the message/body portion is not guaranteed to be free of embedded `0x00` bytes when the object is crafted directly (e.g. via `git hash-object -w --stdin -t commit` or a fabricated pack/loose object supplied by a malicious remote, rather than via the `git commit -m` CLI, which is limited by C-string argument passing). `git log`'s pretty-print machinery operates on NUL-safe `strbuf` buffers and will emit the raw bytes of `%b` unchanged. Consequently, a commit whose body contains a stray `0x00` will inject an *extra* split point into `records`, permanently shifting the index alignment (`i += keys.length`) for that commit's remaining fields and for **every subsequent commit** returned in the same `git log` invocation — with no exception, warning, or bounds check anywhere in `createLogParser`, exactly mirroring the "excess/misaligned data silently ignored/misattributed" behavior described in the report.

### Impact Explanation
Once misaligned, `commit.sha`, `commit.parents`, `commit.author`, and `commit.trailers` for subsequent commits are populated from the wrong byte ranges. These values are consumed for state-changing operations, not just display: `getCommits` output feeds `git-store.ts` and downstream operations such as `cherry-pick.ts`, `revert.ts`, `reorder.ts`, and `squash.ts`, which act on `commit.sha` and reconstruct ancestry from `commit.parents`. A misparsed SHA or parent chain can cause Desktop to select, cherry-pick, revert, reorder, or squash the wrong commit relative to what is displayed/intended by the user — i.e., silent corruption of what gets committed/pushed, sourced entirely from an attacker-crafted repository object with no user error shown.

### Likelihood Explanation
The attacker precondition matches the accepted threat model exactly: the attacker only needs to control a commit object in a repository the victim clones or fetches (crafting a commit object with an embedded NUL byte in its message is possible via low-level git plumbing, bypassing the CLI's C-string message restriction). No local access, admin rights, or social engineering beyond "clone/fetch this repository" is required. The likelihood of successful *exploitation to a specific desired corruption* is moderate — the attacker controls that misalignment occurs and roughly where, but precisely engineering which downstream SHA/parents values line up requires care given git's own field ordering; still, the invariant break itself (silent misparse without any validation) is deterministic and directly reachable.

### Recommendation
In `createLogParser` (and `createForEachRefParser`), validate that `records.length` is an exact multiple of `keys.length` (with the expected leading/trailing empty markers accounted for) and throw/reject if not, rather than silently truncating or misaligning. Additionally, prefer a git output mode that unambiguously escapes or forbids NUL within field content, or request git to reject/normalize commit messages containing NUL, and reject any parsed commit record whose reconstructed length or field count deviates from expectations.

### Proof of Concept
1. Attacker creates a repository and crafts a commit object directly (not via `git commit -m`) whose message/body contains a literal `0x00` byte, e.g. via:
   ```
   printf 'tree <TREE_SHA>\nauthor A <a@x> 0 +0000\ncommitter A <a@x> 0 +0000\n\nfoo\x00bar' | git hash-object -w -t commit --stdin
   ```
   and points a branch/ref at this commit (or a later commit whose ancestry includes it).
2. Victim clones/fetches this repository into Desktop.
3. Desktop calls `getCommits`, which runs `git log ... -z --format=<9 fields joined by %x00>` and passes the buffer to `createLogParser(...).parse()`. [6](#0-5) 
4. The embedded `0x00` in the crafted commit's body inserts an extra element into `records`, shifting the fixed-stride assignment `entry[key] = records[i + ix]` for all subsequent commits in the returned array — `sha`, `parents`, `author`, etc. for later (unrelated, legitimate) commits get populated from the wrong byte offsets, with no error thrown.
5. Any Desktop feature that acts on these `Commit` objects (history view actions, cherry-pick, revert, reorder, squash) can now target the wrong commit/parent SHA, corrupting the user's intended git operation without any indication of failure.

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
