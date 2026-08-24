Based on my investigation, I found a genuine identity-spoofing analog in the `getBotLogin` / bot-avatar resolution path — an attacker who controls commit content (author/committer email in a cloned/fetched repository) can impersonate a trusted GitHub bot identity, causing Desktop to display the official bot avatar/name for an attacker-authored commit.

### Title
Attacker-controlled commit email spoofs verified bot identity/avatar via stealth-email pattern match - (File: app/src/ui/lib/avatar.tsx)

### Summary
The mail-server report's broken invariant is: the recipient's client displayed a sender identity ("bitsCrunch") that was not cryptographically or structurally verified before being rendered as trustworthy, letting an attacker impersonate a known/trusted brand. The closest reachable Desktop analog is bot-identity impersonation in the commit-avatar pipeline: `getBotLogin` in `app/src/ui/lib/avatar.tsx` derives a "trusted bot" login purely from parsing the commit author/committer **email string** with a regex, with no verification that the commit actually originated from GitHub's web flow or the real bot account.

### Finding Description
`getBotLogin` in `app/src/ui/lib/avatar.tsx:47-56` calls `parseStealthEmail(user.email, endpoint)` and treats any match whose `login` ends with `[bot]` as a legitimate bot: [1](#0-0) 

`parseStealthEmail` in `app/src/lib/email.ts:143-153` only checks that the email matches the regex `^(?:(\d+)\+)?(.+?)@(users\.noreply\..+)$` and that the host suffix matches the endpoint's noreply host — it performs no lookup against the actual numeric account ID or ownership: [2](#0-1) 

Once `getBotLogin` returns a `[bot]`-suffixed login, the avatar cache (`botAvatarCache` in `app/src/ui/lib/avatar.tsx:58-94`) calls the API (`api.fetchUser(login)`) using *the current signed-in user's own account/token* to fetch and display that bot's real avatar for the commit: [3](#0-2) 

Because `commit.author`/`commit.committer` values come directly from `git log`/`git show` output of a **cloned or fetched repository** — fully attacker-controlled data — an attacker can craft a commit with an author email such as `41898282+github-actions[bot]@users.noreply.github.com` (the real `github-actions[bot]` numeric ID, listed in `app/src/models/dot-com-bots.ts:24`) even though the commit was never produced by GitHub Actions. Desktop's `getAvatarUsersForCommit` (`app/src/models/avatar.ts:53-79`) and the separate Copilot-name-rewrite logic (`app/src/models/avatar.ts:81-94`) both rely on the same unauthenticated stealth-email string match to decide how to label/avatar the commit author, with no server-side verification that the commit was actually authored via GitHub's web flow (the only real verification, `isWebFlowCommitter`, is a *committer*-name/email heuristic in `app/src/lib/web-flow-committer.ts:15-49`, and is a separate, narrower check that does not gate the bot-avatar path). [4](#0-3) [5](#0-4) 

### Impact Explanation
This lets an attacker who controls a repository/branch/commit that a victim clones, fetches, or checks out in Desktop (e.g., a malicious fork, PR branch, or tampered history) impersonate a known-trusted bot (`github-actions[bot]`, `dependabot[bot]`, `Copilot`, etc.) in the commit/history UI, including displaying that bot's real avatar. This is analogous to the "impersonate a trusted sender to drive user trust/behavior" pattern in the report — a victim reviewing history/diffs could be misled into trusting a malicious commit as an automated/official one, potentially approving a merge, PR, or further action they would not take from an unknown human author. It does not itself achieve code execution but undermines a security-relevant UI signal (author attribution/trust) the same way the spoofed `noreply@bitscrunch.com` email undermined sender trust.

### Likelihood Explanation
High: authoring a commit with an arbitrary email is trivial (`git commit --author`), no push permission to the real GitHub org/bot account is required, and the numeric bot IDs used in the stealth-email format are public knowledge, hardcoded in `app/src/models/dot-com-bots.ts:23-31`. Any victim who fetches/clones the attacker's branch and views its history in Desktop is affected — no unusual user action is required beyond normal repository browsing. [6](#0-5) 

### Recommendation
Do not treat a commit's raw `[bot]`-suffixed stealth email as sufficient proof of bot identity. Gate bot-avatar/name resolution behind `isWebFlowCommitter`-style verification (i.e., only trust the identity for the *committer* field when the commit was actually produced by GitHub's web flow, not merely because the author/committer email string matches the expected pattern), or fetch and compare against the actual known bot user ID/login pair from the GitHub API rather than trusting client-side regex parsing of attacker-supplied commit metadata.

### Proof of Concept
1. Create a local repository and commit with a forged author identity:
   `git commit --allow-empty -m "ci: update" --author="github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>"`
2. Push/host this commit in a repository the victim adds/fetches into GitHub Desktop (e.g., a fork or feature branch).
3. In Desktop, open the commit in History — `getAvatarUserFromAuthor` (app/src/models/avatar.ts:29-39) builds an `IAvatarUser` from the forged email, `getBotLogin` (app/src/ui/lib/avatar.tsx:47-56) matches it as `github-actions[bot]`, and the app fetches/renders the real `github-actions[bot]` avatar for the attacker-authored commit, visually indistinguishable from a genuine Actions-authored commit.

### Citations

**File:** app/src/ui/lib/avatar.tsx (L47-56)
```typescript
const getBotLogin = (user: IAvatarUser) => {
  const { endpoint } = user
  if (user.avatarURL !== undefined || endpoint === null) {
    return undefined
  }

  const match = parseStealthEmail(user.email, endpoint)

  return match?.login?.endsWith('[bot]') ? match.login : undefined
}
```

**File:** app/src/ui/lib/avatar.tsx (L58-94)
```typescript
const botAvatarCache = new ExpiringOperationCache<
  { user: IAvatarUser; accounts: ReadonlyArray<Account> },
  IAvatarUser
>(
  ({ user }) => `${user.endpoint}:${user.email}`,
  async ({ user, accounts }) => {
    const { endpoint } = user
    if (user.avatarURL !== undefined || endpoint === null) {
      throw new Error('Avatar URL already resolved or endpoint is missing')
    }

    const account = accounts.find(a => a.endpoint === user.endpoint)

    if (!account) {
      throw new Error('No account found for endpoint')
    }

    const login = getBotLogin(user)

    if (!login) {
      throw new Error('Email does not appear to be a bot email')
    }

    const api = new API(endpoint, account.token)
    const apiUser = await api.fetchUser(login)

    if (!apiUser?.avatar_url) {
      throw new Error('No avatar url returned from API')
    }

    return { ...user, avatarURL: apiUser.avatar_url }
  },
  ({ user }) =>
    user.endpoint && isGHE(user.endpoint)
      ? offsetFrom(0, 50, 'minutes')
      : Infinity
)
```

**File:** app/src/lib/email.ts (L141-153)
```typescript
const StealthEmailRegexp = /^(?:(\d+)\+)?(.+?)@(users\.noreply\..+)$/i

export const parseStealthEmail = (email: string, endpoint: string) => {
  const stealthEmailHost = getStealthEmailHostForEndpoint(endpoint)
  const match = StealthEmailRegexp.exec(email)

  if (!match || stealthEmailHost !== match[3]) {
    return null
  }

  const [, id, login] = match
  return { id: id ? parseInt(id, 10) : undefined, login }
}
```

**File:** app/src/models/avatar.ts (L53-94)
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
```

**File:** app/src/lib/web-flow-committer.ts (L15-49)
```typescript
export function isWebFlowCommitter(
  commit: Commit,
  gitHubRepository: GitHubRepository
) {
  if (!gitHubRepository) {
    return false
  }

  const endpoint = gitHubRepository.owner.endpoint
  const { name, email } = commit.committer

  if (
    endpoint === getDotComAPIEndpoint() &&
    name === 'GitHub' &&
    email === 'noreply@github.com'
  ) {
    return true
  }

  if (endpoint !== getDotComAPIEndpoint() && name === 'GitHub Enterprise') {
    // We can't assume that the email address will match any specific format
    // here since the web flow committer email address on GHES is the same as
    // the noreply email which can be configured by domain administrators so
    // we'll just have to assume that for a GitHub hosted repository (but not
    // GitHub.com) a commit author of the name 'GitHub Enterprise' is the web
    // flow author.
    //
    // Hello future contributor: Turns out the web flow committer name is based
    // on the "flavor" of GitHub so it's possible that you're here wondering why
    // this isn't working and chances are it's because we've updated the
    // GHES branding or introduced some new flavor.
    return true
  }

  return false
```

**File:** app/src/models/dot-com-bots.ts (L23-31)
```typescript
export const dependabotBot = dotComBot('dependabot[bot]', 49699333, 29110)
export const actionsBot = dotComBot('github-actions[bot]', 41898282, 15368)
export const githubPagesBot = dotComBot('github-pages[bot]', 52472962, 34598)
// https://github.com/apps/copilot-pull-request-reviewer
export const copilotPRReviewerBot = dotComBot('Copilot', 175728472, 946600)
// https://github.com/apps/copilot-swe-agent
export const copilotSweAgentBot = dotComBot('Copilot', 198982749, 1143301)
// https://github.com/apps/github-copilot-cli
export const copilotCliBot = dotComBot('Copilot', 223556219, 1693627)
```
