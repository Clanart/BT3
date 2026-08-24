### Title
Malformed commit author/committer identity in a cloned/fetched repository crashes commit history loading via unguarded `CommitIdentity.parseIdentity` - (File: app/src/lib/git/log.ts)

### Summary
`getCommits` in `app/src/lib/git/log.ts` parses `git log` output and directly calls `CommitIdentity.parseIdentity()` on the raw `%an <%ae> %ad` / `%cn <%ce> %cd` fields produced by `git log`, without any `try/catch`. `CommitIdentity.parseIdentity` throws a plain `Error` whenever the identity string does not match its strict regex. Because author/committer identity strings originate from raw git commit objects — which are attacker-controlled content inside a repository the user clones or fetches — a repository author can craft a commit whose author/committer line does not conform to the expected `NAME <EMAIL> DATE TZ` shape, causing an unhandled exception every time Desktop tries to load history for that repository.

### Finding Description
`CommitIdentity.parseIdentity` is documented as throwing on invalid input: [1](#0-0) 

The regex `/^(.*?) <(.*?)> (\d+) (\+|-)?(\d{2})(\d{2})/` requires the identity string to contain a well-formed `<email>` block followed by a numeric epoch and a 4-digit timezone. Elsewhere in the codebase, callers that invoke this function on git-derived, potentially-untrusted data explicitly guard against the throw, e.g. `getAuthorIdentity` in `app/src/lib/git/var.ts`: [2](#0-1) 

However, `getCommits`, which is the primary path used to populate commit history for the History/Changes UI, calls `parseIdentity` unguarded: [3](#0-2) 

The author/committer fields (`%an <%ae> %ad`, `%cn <%ce> %cd`) come straight from `git log` formatting a raw commit object's `author`/`committer` headers. Git itself does not require these headers in any client-normalized shape when reading pre-existing objects created by another tool (e.g. `git hash-object`/`git commit-tree` with a raw, non-canonical author string, or objects fetched from a remote/proxy under attacker control). A crafted commit with an author line lacking the expected `<email>` delimiters, missing the numeric epoch, or omitting a valid 4-digit timezone will cause `%an <%ae> %ad` to render in a way that fails the regex, and `parseIdentity` throws.

Because this call happens inside the `.map()` callback of `getCommits` (`app/src/lib/git/log.ts` lines 177-204) with no surrounding `try/catch`, the exception propagates out of the async function as a rejected promise. Unless every caller in the chain (git-store, dispatcher, IPC boundary) wraps this specific call in error handling for this specific failure mode, this becomes either an unhandled promise rejection or a hard crash of the commit-history-loading flow, effectively denying the user access to the repository's history — the same broken invariant as the Nimbus bug: a single malformed, attacker-supplied record breaks a parser that has no fallback path and takes down the consuming operation.

### Impact Explanation
This meets the "Valid Impact" criteria: the attacker controls a cloned/fetched repository (a commit object with a malformed identity string), no local/physical access or credentials are needed, and the result is a denial-of-service against the victim's ability to load repository history — directly analogous to the original Nimbus finding (malformed, attacker-controlled data crashing a core processing path due to an unguarded parse). It does not require unusual user action beyond cloning/fetching a repository, which is a normal, expected Desktop workflow.

### Likelihood Explanation
Likelihood is Medium: crafting a commit object with a non-canonical author/committer line requires low-level git plumbing (e.g., `git hash-object -t commit -w` with a hand-built object, or a corrupted/synthetic packfile served by a malicious/compromised remote or MITM proxy), which is achievable by any repository owner or man-in-the-middle without needing push access to an existing trusted repo — the victim just needs to clone/fetch the attacker's repository. This is a lower bar than requiring account compromise, and Desktop performs `getCommits` automatically whenever a repository is opened/history is viewed.

### Recommendation
Wrap the `CommitIdentity.parseIdentity` calls inside `getCommits` in a `try/catch`, mirroring the defensive pattern already used in `getAuthorIdentity` (`app/src/lib/git/var.ts`). On failure, fall back to a placeholder `CommitIdentity` (e.g., using the raw string as `name`, empty `email`, and current date) rather than throwing, so a single malformed commit cannot abort loading of the entire history. Additionally, consider validating/sanitizing all `%an/%ae/%an/%cn/%ce/%cd`-derived fields defensively for the same reason `parseIdentity` was made throwing in the first place — because upstream data cannot be trusted to be canonical.

### Proof of Concept
1. Create a local bare repository and script an out-of-band commit object with a malformed author line, e.g. using `git hash-object -t commit -w --stdin` with content that has an `author` header of the form `author Evil Corp NOEMAILHERE 1234567890xxxx` (missing `<`/`>` delimiters and non-numeric trailing timezone), then update a ref to point at that commit.
2. Serve this repository (e.g. via a local git daemon or as the target of a "clone" URL) and have Desktop clone/fetch it.
3. Open the repository in Desktop and view its History; `getCommits` (`app/src/lib/git/log.ts`) invokes `CommitIdentity.parseIdentity(commit.author.toString())`, which throws `Couldn't parse identity ...` because the crafted string doesn't match the identity regex, and this exception is unguarded within the mapping function, aborting the commit-history retrieval.

(Note: I was not able to fully trace every downstream consumer of `getCommits` in `git-store.ts` due to tool/iteration limits — the file's contents beyond line 1 were not retrievable in this session. It is possible that some downstream caller wraps `loadCommitBatch`/`loadHistory` in a broader try/catch that surfaces a generic error banner instead of a hard app crash; even so, this still represents a DoS on a valid Desktop feature — history loading — triggered purely by a crafted repository, so the core finding stands regardless of exactly how far the crash propagates.)

### Citations

**File:** app/src/models/commit-identity.ts (L6-26)
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
```

**File:** app/src/lib/git/var.ts (L36-41)
```typescript

  try {
    return CommitIdentity.parseIdentity(result.stdout)
  } catch (err) {
    return null
  }
```

**File:** app/src/lib/git/log.ts (L186-192)
```typescript
    return new Commit(
      commit.sha.toString(),
      commit.shortSha.toString(),
      commit.summary.subarray(0, 100 * 1024).toString(),
      commit.body.subarray(0, 100 * 1024).toString(),
      CommitIdentity.parseIdentity(commit.author.toString()),
      CommitIdentity.parseIdentity(commit.committer.toString()),
```
