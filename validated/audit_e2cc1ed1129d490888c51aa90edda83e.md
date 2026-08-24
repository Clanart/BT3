Based on my investigation, the vulnerability path is real and unmitigated in the code I could inspect, though I was unable to execute git itself to empirically confirm git's exact byte-for-byte trailer-formatting behavior on embedded-newline values (no terminal access in ask-only mode). I flag that residual uncertainty below.

### Title
Unsanitized attacker-controlled mentionable `email`/`name` allows forged `Co-Authored-By` trailer injection into commit messages - (File: `app/src/ui/changes/commit-message.tsx`)

### Summary
`GitHubUserStore.updateMentionables` stores the `email`/`name`/`login` fields from the GitHub `mentionables/users` API response verbatim, with no validation that they are free of newline/control characters. That data flows unsanitized through the autocomplete UI into a `KnownAuthor`, and `CommitMessage.getCoAuthorTrailers` builds a raw trailer value string `${a.name} <${a.email}>` from it, which is handed to `mergeTrailers`/`git interpret-trailers` and baked directly into the final commit message.

### Finding Description
The data flow is:
1. `GitHubUserStore.updateMentionables` maps the raw API fields directly into the local mentionable cache, with no sanitization of `name`/`login`/`email`: [1](#0-0) 
2. `IAPIMentionableUser.email`/`.name` are typed as free-form strings with no format constraint enforced client-side: [2](#0-1) 
3. `UserAutocompletionProvider.getUserAutocompletionItems`/`userToHit` surfaces the cached email/name verbatim to the co-author autocomplete UI: [3](#0-2) 
4. `AuthorInput.authorFromUserHit`/`getEmailAddressForUser` copies `user.email` straight into a `KnownAuthor.email` with no sanitization: [4](#0-3) 
5. `CommitMessage.getCoAuthorTrailers` string-interpolates the attacker-controlled `name`/`email` into a raw trailer value: [5](#0-4) 
6. That trailer is passed to `formatCommitMessage` → `mergeTrailers`, which passes the value as a single `--trailer token=value` argv entry to `git interpret-trailers`: [6](#0-5) [7](#0-6) 

No code on this path strips or rejects `\n`/`\r` (or other control characters) from `a.name`/`a.email` before it is embedded as a trailer value. Because the value is passed as a single process argument (not through a shell), this is not classic shell/argv injection — the risk is specifically that an embedded newline in the value becomes a literal newline inside the final commit-message text produced by `git interpret-trailers`, and a second line crafted to look like `Co-Authored-By: attacker <x@y>` would then read as an additional, independent trailer when the message is later parsed (e.g., by `git interpret-trailers --parse` used elsewhere in this codebase at `app/src/lib/stores/git-store.ts:798`, or by GitHub's own trailer parsing on push) — resulting in a forged attribution trailer that the user never intended to add.

### Impact Explanation
If exploitable, this allows an attacker who controls the mentionables API response (a GitHub API object, which is in-scope per the Valid Impact section) to silently corrupt what the user commits: an extra `Co-Authored-By`/`Signed-off-by`-style trailer can be smuggled into the commit message the user believes only credits the one co-author they explicitly picked from the autocomplete list. This is a "silent corruption of what the user commits" scenario explicitly called out as valid impact.

### Likelihood Explanation
Likelihood requires the attacker to control (or MITM/serve a malicious response for) the `repos/{owner}/{name}/mentionables/users` endpoint content for a repository the victim has cloned — e.g., a malicious/compromised GitHub Enterprise Server instance, or a proxy/response the client trusts. It does not require any unusual user action beyond selecting a co-author from the normal autocomplete dropdown, which is expected user behavior in this feature.

### Recommendation
- Reject or strip `\n`/`\r`/other control characters from `email`, `name`, and `login` in `GitHubUserStore.updateMentionables` / `API.fetchMentionables` before caching.
- Alternatively/additionally, validate/sanitize `a.name`/`a.email` in `CommitMessage.getCoAuthorTrailers` before constructing the trailer value, e.g., reject values containing `\n`/`\r`.
- Consider using `git interpret-trailers`'s own escaping semantics conservatively (e.g., verifying that a single `--trailer` value cannot introduce additional trailer lines) as defense in depth.

### Proof of Concept
Conceptually (not executed by me — I have no terminal/git access in this session):
1. Seed the mentionables cache (or mock `API.fetchMentionables`) with a user whose `email` is `"x@y>\nCo-Authored-By: attacker <a@b.com"` (or a `name` with embedded `\n`).
2. Select that user as a co-author via `AuthorInput`, causing `authorFromUserHit` to build a `KnownAuthor` with that raw email.
3. Call `CommitMessage.getCoAuthorTrailers()` and then `formatCommitMessage`/`mergeTrailers` (as `app/test/unit/format-commit-message-test.ts` does) and assert the resulting message string contains two `Co-Authored-By:` lines, the second one being the forged, unintended one.

Caveat: I was able to trace and confirm the complete absence of sanitization along this data path in the source, but I could not empirically run `git interpret-trailers` to confirm the exact literal output it produces for a `--trailer` value containing an embedded raw `\n` (e.g., whether git folds/indents the continuation line instead of leaving it as a bare new line). This is the one detail needed to convert this from "very likely exploitable based on code" to "confirmed exploitable," and it should be verified with the PoC above in an environment with git installed before treating this as fully confirmed.

### Citations

**File:** app/src/lib/stores/github-user-store.ts (L106-118)
```typescript
    const { endpoint } = account

    const mentionables = response.users.map(u => {
      const { name, login, avatar_url: avatarURL } = u
      const email = u.email || getLegacyStealthEmailForUser(login, endpoint)
      return { name, login, email, avatarURL }
    })

    await this.database.updateMentionablesForRepository(
      repository.dbID,
      mentionables,
      response.etag
    )
```

**File:** app/src/lib/api.ts (L283-308)
```typescript
/** The users we get from the mentionables endpoint. */
export interface IAPIMentionableUser {
  /**
   * A url to an avatar image chosen by the user
   */
  readonly avatar_url: string

  /**
   * The user's attributable email address or null if the
   * user doesn't have an email address that they can be
   * attributed by
   */
  readonly email: string | null

  /**
   * The username or "handle" of the user
   */
  readonly login: string

  /**
   * The user's real name (or at least the name that the user
   * has configured to be shown) or null if the user hasn't provided
   * a real name for their public profile.
   */
  readonly name: string | null
}
```

**File:** app/src/ui/autocompletion/user-autocompletion-provider.tsx (L47-58)
```typescript
function userToHit(
  repository: GitHubRepository,
  user: IMentionableUser
): UserHit {
  return {
    kind: 'known-user',
    username: user.login,
    name: user.name,
    email: user.email,
    endpoint: repository.endpoint,
  }
}
```

**File:** app/src/ui/lib/author-input/author-input.tsx (L73-93)
```typescript
function getEmailAddressForUser(user: KnownUserHit) {
  return user.email && user.email.length > 0
    ? user.email
    : getLegacyStealthEmailForUser(user.username, user.endpoint)
}

/**
 * Convert a IUserHit object which is returned from
 * user-autocomplete-provider into a KnownAuthor object.
 *
 * If the IUserHit object lacks an email address we'll
 * attempt to create a stealth email address.
 */
function authorFromUserHit(user: KnownUserHit): KnownAuthor {
  return {
    kind: 'known',
    name: user.name || user.username,
    email: getEmailAddressForUser(user),
    username: user.username,
  }
}
```

**File:** app/src/ui/changes/commit-message.tsx (L577-585)
```typescript
  private getCoAuthorTrailers() {
    const { coAuthors } = this.props
    const token = 'Co-Authored-By'
    return this.isCoAuthorInputEnabled
      ? coAuthors
          .filter(isKnownAuthor)
          .map(a => ({ token, value: `${a.name} <${a.email}>` }))
      : []
  }
```

**File:** app/src/lib/format-commit-message.ts (L18-36)
```typescript
export async function formatCommitMessage(
  repository: Repository,
  context: ICommitContext
) {
  const { summary, description, trailers } = context

  // Git always trim whitespace at the end of commit messages
  // so we concatenate the summary with the description, ensuring
  // that they're separated by two newlines. If we don't have a
  // description or if it consists solely of whitespace that'll
  // all get trimmed away and replaced with a single newline (since
  // all commit messages needs to end with a newline for git
  // interpret-trailers to work)
  const message = `${summary}\n\n${description || ''}\n`.replace(/\s+$/, '\n')

  return trailers !== undefined && trailers.length > 0
    ? mergeTrailers(repository, message, trailers)
    : message
}
```

**File:** app/src/lib/git/interpret-trailers.ts (L152-176)
```typescript
export async function mergeTrailers(
  repository: Repository,
  commitMessage: string,
  trailers: ReadonlyArray<ITrailer>,
  unfold: boolean = false
) {
  const args = ['interpret-trailers']

  // See https://github.com/git/git/blob/ebf3c04b262aa/Documentation/git-interpret-trailers.txt#L129-L132
  args.push('--no-divider')

  if (unfold) {
    args.push('--unfold')
  }

  for (const trailer of trailers) {
    args.push('--trailer', `${trailer.token}=${trailer.value}`)
  }

  const result = await git(args, repository.path, 'mergeTrailers', {
    stdin: commitMessage,
  })

  return result.stdout
}
```
