### Title
Delimiter-less concatenation of attacker-controlled commit `name`/`email` allows author-identity collision, silently merging or dropping distinct commit authors in the UI - ([File: app/src/models/avatar.ts])

### Summary
`getAvatarUsersForCommit` deduplicates the set of avatar users for a commit (author, co-authors, committer) by building a `Map` keyed on the plain string concatenation `x.name + x.email`, with no separator or length-prefix between the two fields: [1](#0-0) 

This is the same broken-invariant class as the reported `DKGResultVerification.verify` bug: two logically distinct fields are `abi.encodePacked`/string-concatenated without a delimiter, so bytes can be shifted from one field to the other while producing an identical combined value.

### Finding Description
`name` and `email` both originate from git commit metadata that is fully attacker-controlled in any repository the user clones or fetches: `CommitIdentity.parseIdentity` extracts them straight from the raw ident string with only minimal constraints (`<`/`>` stripped, otherwise arbitrary strings), and `GitAuthor` (co-author trailers, `Co-authored-by:`) likewise accepts arbitrary strings. [2](#0-1) 

Because the two arbitrary-length strings are concatenated without a separator, an attacker who controls the commit content (author identity, committer identity, and/or `Co-authored-by:` trailers) can craft two different `(name, email)` pairs that hash/key to the same string, e.g. `name="AB", email="C@x.com"` vs. `name="A", email="BC@x.com"` both yield the key `"ABC@x.com"`. When such a collision is engineered against a legitimate account's real identity string, the `Map` in `getAvatarUsersForCommit` will keep only one entry for the key and drop/overwrite the other: [3](#0-2) 

This function is the sole source of truth for author/avatar display across the commit history, commit list, and commit-summary UI, so the corrupted, merged identity list flows directly into rendered UI: [4](#0-3) [5](#0-4) 

No existing guard validates field lengths or separates the two fields before concatenation — unlike `isAttributableEmailFor`/`getStealthEmailForUser`, which correctly interpose a fixed `@`/`+` structure that cannot be forged the same way because the email domain suffix is checked separately, this dedup key has no such structure.

### Impact Explanation
The corrupted value is the deduplicated `avatarUsersByIdentity` map: two distinct commit authors/co-authors can be silently collapsed into one displayed identity, or a forged co-author can silently take the place of the real committer entry in the list rendered to the user. Because avatar/author attribution is a trust signal users rely on when reviewing incoming commits/PRs (e.g., to decide whether a commit was written by a known collaborator or is a webflow/bot commit), an attacker who crafts a malicious commit in a repo the victim fetches/clones can manipulate which author identity is displayed, potentially hiding their own attribution behind a colliding legitimate-looking name/email pair. This is a UI/trust-decision corruption rather than a direct code-execution, credential-exfiltration, or repository-corruption bug — it does not change what the user actually commits or pushes, only what identity information is displayed about pre-existing commits from an untrusted source.

### Likelihood Explanation
Exploitation only requires the attacker to push/host a git commit with crafted author/committer name+email or `Co-authored-by:` trailers and get the victim to fetch/clone the repository — no additional user interaction or privileges are needed. Constructing a byte-shift collision between two attacker-chosen strings is trivial (concatenation collisions are easy to construct at will). The harder part is engineering a collision against a *specific victim's real* name/email that the victim would recognize, which requires the attacker to know that string, but the primitive itself (silent identity merging via unkeyed concatenation) is real and directly exploitable for any two identities the attacker controls (e.g., merging two distinct co-authors into one, or making a forged `Copilot`-name identity collide with a trusted user's).

### Recommendation
Use a proper compound key instead of naive concatenation, e.g. `JSON.stringify([x.name, x.email])`, a key built with an unambiguous separator plus length prefixes (`` `${x.name.length}:${x.name}${x.email.length}:${x.email}` ``), or key on a tuple/array in a `Map`-like structure that doesn't coerce to a colliding string. The same fix should be applied to any other unseparated concatenations of independently attacker-controlled fields used for identity/equality purposes in this file and related avatar-cache code (e.g. `app/src/ui/lib/avatar.tsx` cache keys such as `` `${user.endpoint}:${user.email}` `` should also be reviewed, though `:` there is less exploitable since endpoints are not attacker-controlled).

### Proof of Concept
1. Attacker creates a commit in a repository with two co-authors via trailers such that:
   - Co-author A: `name = "AB"`, `email = "C@example.com"`
   - Co-author B: `name = "A"`, `email = "BC@example.com"`
2. Both produce the identical `getAvatarUsersForCommit` map key `"ABC@example.com"`.
3. Victim clones/fetches the repository in GitHub Desktop and views the commit in the history list (`CommitListItem`) or expanded commit summary.
4. Only one of the two co-authors is shown, with the `IAvatarUser` object from whichever entry the `Map` insertion order preserves (the second processed key overwrites the first) — the other author's identity and avatar silently disappear from the UI, misrepresenting the commit's actual authorship to the reviewer.

### Citations

**File:** app/src/models/avatar.ts (L53-100)
```typescript
export function getAvatarUsersForCommit(
  gitHubRepository: GitHubRepository | null,
  commit: Commit
) {
  const avatarUsers = []

  avatarUsers.push(getAvatarUserFromAuthor(commit.author, gitHubRepository))
  avatarUsers.push(
    ...commit.coAuthors.map(x => getAvatarUserFromAuthor(x, gitHubRepository))
  )

  const coAuthoredByCommitter = commit.coAuthors.some(
    x => x.name === commit.committer.name && x.email === commit.committer.email
  )

  const webFlowCommitter =
    gitHubRepository !== null && isWebFlowCommitter(commit, gitHubRepository)

  if (
    !commit.authoredByCommitter &&
    !webFlowCommitter &&
    !coAuthoredByCommitter
  ) {
    avatarUsers.push(
      getAvatarUserFromAuthor(commit.committer, gitHubRepository)
    )
  }

  // Copilot sometimes uses the copilot-swe-agent[bot] as its committer identity name.
  // Dotcom always resolves the user and shows the login leading to all Copilot commits
  // to show up as Copilot, we should do the same.
  if (gitHubRepository) {
    for (const au of avatarUsers) {
      if (
        au.name === 'copilot-swe-agent[bot]' &&
        parseStealthEmail(au.email, gitHubRepository.endpoint)?.login ===
          'Copilot'
      ) {
        au.name = 'Copilot'
      }
    }
  }

  const avatarUsersByIdentity = new Map<string, IAvatarUser>(
    avatarUsers.map(x => [x.name + x.email, x])
  )

  return [...avatarUsersByIdentity.values()]
```

**File:** app/src/models/commit-identity.ts (L10-50)
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

    // The RAW option never uses alphanumeric timezone identifiers and in my
    // testing I've never found it to omit the leading + for a positive offset
    // but the docs for strprintf seems to suggest it might on some systems so
    // we're playing it safe.
    const tzSign = m[4] === '-' ? '-' : '+'
    const tzHH = m[5]
    const tzmm = m[6]

    const tzMinutes = parseInt(tzHH, 10) * 60 + parseInt(tzmm, 10)
    const tzOffset = tzMinutes * (tzSign === '-' ? -1 : 1)

    return new CommitIdentity(name, email, date, tzOffset)
  }
```

**File:** app/src/ui/history/commit-list-item.tsx (L64-73)
```typescript
  public constructor(props: ICommitProps) {
    super(props)

    this.state = {
      avatarUsers: getAvatarUsersForCommit(
        props.gitHubRepository,
        props.commit
      ),
    }
  }
```

**File:** app/src/ui/history/expandable-commit-summary.tsx (L114-121)
```typescript
  const allAvatarUsers = selectedCommits.flatMap(c =>
    getAvatarUsersForCommit(repository.gitHubRepository, c)
  )

  const avatarUsers = uniqWith(
    allAvatarUsers,
    (a, b) => a.email === b.email && a.name === b.name
  )
```
