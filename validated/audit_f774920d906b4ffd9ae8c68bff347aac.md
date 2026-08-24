## Analysis

The Foundry advisory is about an unguarded, format-dependent parser (`jsonwebtoken`) that throws an unrecoverable error (a Rust panic) when it processes a value it wasn't defensively initialized to handle. The transferable bug class for GitHub Desktop is: **a strict, regex-based parser of git-native metadata that throws on malformed input, called directly (without a try/catch) from a hot path that processes commits originating from a repository the user just cloned or fetched — i.e., attacker-controlled data.**

`CommitIdentity.parseIdentity` is exactly this kind of parser. It requires the author/committer line to match `^(.*?) <(.*?)> (\d+) (\+|-)?(\d{2})(\d{2})` and explicitly documents that it **throws** if the string doesn't match: [1](#0-0) 

Git does not enforce this shape at the object level — commit objects are created by low-level plumbing (`git commit-tree`, `hash-object` + `update-ref`, or a maliciously crafted pack) and can contain an author/committer identity line with an empty name/email, a non-numeric or truncated timestamp, or no `<`/`>` delimiters at all. `git log --format=%an <%ae> %ad` will faithfully reproduce whatever bytes are in the object, including a broken identity, since Git's own `ident.c` logic (referenced in the comments) only partially sanitizes `<`/`>` and does not guarantee the regex Desktop expects.

`getCommits`, which is the primary function Desktop uses to load commit history for **any** opened/cloned/fetched repository, calls `CommitIdentity.parseIdentity` directly inside the `.map()` that builds `Commit` objects, with no try/catch: [2](#0-1) 

Compare this to `getAuthorIdentity` in `var.ts`, which calls the exact same throwing function but wraps it defensively: [3](#0-2) 

`getAuthors` in the same file has the identical unguarded pattern: [4](#0-3) 

This is the same root cause pattern as the CVE seed: a strict/validating decoder is invoked on attacker-influenced data in a path that was never hardened to catch its own documented failure mode, so the exception propagates uncaught.

### Title
Unhandled exception in `CommitIdentity.parseIdentity` when loading history from a repository with a malformed commit author/committer line - (File: `app/src/lib/git/log.ts`)

### Summary
`getCommits` (and `getAuthors`) parse the raw `%an <%ae> %ad` / `%cn <%ce> %cd` identity strings emitted by `git log` using `CommitIdentity.parseIdentity`, which throws when the string doesn't match a specific regex. Unlike `getAuthorIdentity`, which wraps the same call in try/catch, `getCommits`/`getAuthors` call it unguarded inside a `.map()`.

### Finding Description
`CommitIdentity.parseIdentity` requires the identity line to match `^(.*?) <(.*?)> (\d+) (\+|-)?(\d{2})(\d{2})` and throws `Error` on any mismatch: [5](#0-4) 

Git objects can contain arbitrary bytes for author/committer identity (created via plumbing commands or a hostile pack the user fetches/clones), and are not required to conform to this shape (empty name, missing angle brackets, non-numeric/garbled timestamp, etc.). Git will output such a commit's identity verbatim through `--format=%an <%ae> %ad`. When Desktop then loads history for that repository via `getCommits`, the parse call is not wrapped in any error handling: [6](#0-5) 
The same applies to `getAuthors`: [7](#0-6) 

### Impact Explanation
Because `getCommits` is the core routine used to populate the History view, blame, comparisons, etc. for any repository the user opens, a single malformed commit inserted anywhere in the reachable history of a cloned/fetched repo will cause an unhandled exception to propagate out of the promise chain. Depending on the caller this manifests as an unhandled rejection (crash-report / degraded state) or a broken history/commit-list feature, effectively letting a hostile remote repository (or a repo obtained via clone URL / "Open in Desktop" deep link) deny the user the ability to view history for that repository, and, if it surfaces as an uncaught error in the renderer, could reach the app's global `uncaughtException`/`unhandledrejection` handling paths: [8](#0-7) 

### Likelihood Explanation
Reachability is straightforward and requires no special privileges: cloning or fetching a repository (or opening one via the `x-github-client://openRepo` deep link handled in `app/src/lib/parse-app-url.ts`) that contains one commit with a hand-crafted identity line is sufficient. This does not require local/physical access, admin rights, or prior malware — it is triggered purely by normal use of Desktop's clone/fetch/browse-history workflow against attacker-supplied repository content.

### Recommendation
Wrap `CommitIdentity.parseIdentity` calls in `getCommits` and `getAuthors` in try/catch (mirroring the existing pattern in `getAuthorIdentity`), falling back to a safe placeholder `CommitIdentity` (e.g., empty name/email, `Date` from commit metadata if available) rather than letting the exception escape, and add a fixture/unit test with a deliberately malformed identity line to confirm history loading remains resilient. Consider relaxing/loosening the regex to tolerate any input, since email/name legitimately cannot be trusted to be well-formed.

### Proof of Concept
1. Create a throwaway git repository.
2. Craft a commit object with a malformed author line, bypassing the porcelain identity formatting, e.g.:
   ```
   GIT_AUTHOR_NAME= GIT_AUTHOR_EMAIL= GIT_AUTHOR_DATE="not-a-date" \
   GIT_COMMITTER_NAME= GIT_COMMITTER_EMAIL= GIT_COMMITTER_DATE="not-a-date" \
   git commit --allow-empty -m "malicious commit"
   ```
   (Git's own porcelain will normalize some of this — a more reliable path is constructing the commit object directly with `git hash-object -w --stdin -t commit` supplying a raw object body whose `author`/`committer` lines omit the `<`/`>` delimiters or timestamp digits, then pointing a branch ref at it with `git update-ref`.)
3. Push/host this repository, or produce a bundle/pack the victim fetches, and have the victim clone/fetch it in Desktop, or send them an `x-github-client://openRepo/...` deep link pointing at it.
4. Open the repository's History tab in Desktop; `getCommits` invokes `CommitIdentity.parseIdentity` on the crafted line, which does not match the expected regex, throws, and the exception propagates unguarded out of `getCommits`.

### Note on confidence
I confirmed the unguarded call sites in `log.ts` and the asymmetry versus `var.ts`'s guarded call, and confirmed `parseAppURL`/deep-link handling exists as an attacker-reachable trigger for opening arbitrary remote URLs. I was not able to fully trace every downstream caller of `getCommits` (e.g., `git-store.ts`'s `loadCommitBatch`, `app-store.ts`) to confirm whether any of them already wrap the call in a catch that would neutralize the crash before it reaches the UI — this would need to be verified in a full Devin session with file access, since the index only returned match locations, not full surrounding context for those files.

### Citations

**File:** app/src/models/commit-identity.ts (L6-36)
```typescript
  /**
   * Parses a Git ident string (GIT_AUTHOR_IDENT or GIT_COMMITTER_IDENT)
   * into a commit identity. Throws an error if identify string is invalid.
   */
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

**File:** app/src/lib/git/log.ts (L186-204)
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
      tags
    )
  })
```

**File:** app/src/lib/git/log.ts (L349-376)
```typescript
/** Get the author identity for the given shas */
export async function getAuthors(repository: Repository, shas: string[]) {
  if (shas.length === 0) {
    return []
  }

  const { stdout } = await git(
    [
      'log',
      '--format=format:%an <%ae> %ad',
      '--no-walk=unsorted',
      '--date=raw',
      '-z',
      '--stdin',
    ],
    repository.path,
    'getAuthors',
    { stdin: shas.join('\n') }
  )

  const authors = stdout.split('\0').map(CommitIdentity.parseIdentity)

  // This can happen if there are duplicate shas in the input, git log will only
  // return the author once for each sha.
  assert.equal(authors.length, shas.length, 'Commit to author mismatch')

  return authors
}
```

**File:** app/src/lib/git/var.ts (L36-42)
```typescript

  try {
    return CommitIdentity.parseIdentity(result.stdout)
  } catch (err) {
    return null
  }
}
```

**File:** app/src/ui/index.tsx (L192-221)
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
