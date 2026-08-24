Confirmed inverted-equality bug in `TeamMentionFilter.filter`.

### Title
Inverted equality check in `TeamMentionFilter` renders team-mention links for orgs that do NOT own the repository, defeating the intended same-org restriction - (File: `app/src/lib/markdown-filters/team-mention-filter.ts`)

### Summary
`TeamMentionFilter.filter` is supposed to only turn a `@org/team` mention found in rendered markdown (issue/PR bodies, comments, commit messages, etc.) into a clickable link when `org` equals the login of the repository's actual owning organization — this is explicitly stated in the inline comment "Team references are only added when the repository owner is the org to prevent linking to a team outside the repositories org." The `continue` (skip-creating-link) branch, however, fires exactly when `org` *matches* the repository owner, and falls through to create a link whenever the org does *not* match, inverting the intended check.

### Finding Description
In `createLinkElement`/`filter`: [1](#0-0) 

```ts
const { org, team } = match.groups
if (
  org === undefined ||
  team === undefined ||
  // Team references are only added when the repository owner is the org to prevent linking to a team outside the repositories org.
  caseInsensitiveEquals(org.slice(1), this.repository.owner.login)
) {
  continue
}

const link = this.createLinkElement(org.slice(1), team.slice(1))
```

The comment states the intent: only link `@org/team` mentions when `org` equals `this.repository.owner.login`. But the boolean is used as a `continue` (i.e., "skip / do NOT create a link") condition. Since `caseInsensitiveEquals(...)` returns `true` when the mentioned org matches the repo's owner, the code skips creating a link *exactly in the one case it should create one*, and instead creates a link for every mention where the org does **not** match the repository's owner — the opposite of the stated invariant. Any `@arbitraryOrg/team` string appearing in markdown originating from a repository owned by any GitHub organization will be turned into a clickable anchor pointing at `${getHTMLURL(endpoint)}/orgs/${org}/teams/${team}` [2](#0-1) , regardless of whether that org has anything to do with the repository being viewed. The existing guard `this.repository.owner.type !== 'Organization'` [3](#0-2)  does not stop this, since it only checks that the *current* repository is org-owned, not that the mentioned org matches it.

This filter runs over markdown rendered from GitHub API objects that are fully attacker-influenced (PR bodies/comments, issue text, commit messages) inside `SandboxedMarkdown`, whose click handler forwards the href to `dispatcher.openInBrowser`/`shell.openExternal` [4](#0-3) , [5](#0-4) .

### Impact Explanation
Because the org-match check is inverted, the filter will manufacture a plausible-looking `@org/team` mention link for *any* org string chosen by an attacker in a PR/issue/comment body of an org-owned repository the victim opens in Desktop — including organizations that have no relationship to that repository. This can be used to build a convincing spoofed link (e.g., "@microsoft/security-team") inside a comment on an unrelated small/attacker-controlled org repo, encouraging the victim to click through to a GitHub org/team page as part of a social-engineering or credential-phishing setup, since Desktop's own logic explicitly claims (and the victim would reasonably assume) that only the repository's real owning org can be referenced this way.

Note: this is a UI-trust/spoofing defect rather than a code-execution, filesystem-escape, credential-exfiltration, or IPC-escape bug. It's borderline against the stated Valid Impact bar (which requires higher-severity outcomes such as code execution, file read/write outside the repo, OAuth/credential compromise, or corruption of what's committed/pushed). I'm flagging it because it is a clear, exact structural analog of the reported inverted-comparison bug (`==` vs `!=`, "continue" skipping the case it should act on), but it does not clearly rise to the severity floor required by the task.

### Likelihood Explanation
Trivial to trigger: any attacker who can leave a comment, open a PR, or push a commit message to a public/shared org-owned repository controls the exact text rendered by this filter, and the victim only needs to view that content in Desktop (issue list, PR view, notifications) for the mis-linked anchor to render.

### Recommendation
Invert the condition so the link is created only when the mention's org matches the repository's owner, and skipped otherwise:
```diff
- caseInsensitiveEquals(org.slice(1), this.repository.owner.login)
+ !caseInsensitiveEquals(org.slice(1), this.repository.owner.login)
```

### Proof of Concept
1. On GitHub, open (or comment on) an issue/PR in a repository owned by organization `real-org`.
2. In the comment body, write text containing `@evil-org/security-team`.
3. Open the repository/issue in GitHub Desktop; `TeamMentionFilter` runs over the rendered markdown.
4. Because `caseInsensitiveEquals('evil-org', 'real-org')` is `false`, the `continue` guard does not trigger, and the filter creates `<a href="https://github.com/orgs/evil-org/teams/security-team">@evil-org/security-team</a>` — a link to an organization entirely unrelated to the repository being viewed, despite the code's explicit comment that this should never happen. [6](#0-5)

### Citations

**File:** app/src/lib/markdown-filters/team-mention-filter.ts (L80-83)
```typescript
      // If the repo is not owned by an org, then there cannot be teams.
      this.repository.owner.type !== 'Organization'
    ) {
      return null
```

**File:** app/src/lib/markdown-filters/team-mention-filter.ts (L94-132)
```typescript
      const { org, team } = match.groups
      if (
        org === undefined ||
        team === undefined ||
        // Team references are only added when the repository owner is the org to prevent linking to a team outside the repositories org.
        caseInsensitiveEquals(org.slice(1), this.repository.owner.login)
      ) {
        continue
      }

      const link = this.createLinkElement(org.slice(1), team.slice(1))
      const refPosition = match.index === 0 ? 0 : match.index + 1
      const textBefore = text.slice(lastMatchEndingPosition, refPosition)
      const textNodeBefore = document.createTextNode(textBefore)
      nodes.push(textNodeBefore)
      nodes.push(link)

      lastMatchEndingPosition = refPosition + org.length + team.length
    }

    const trailingText = text.slice(lastMatchEndingPosition)
    if (trailingText !== '') {
      nodes.push(document.createTextNode(trailingText))
    }

    return nodes
  }

  /**
   * Method to create the user mention anchor.
   **/
  private createLinkElement(org: string, team: string) {
    const baseHref = getHTMLURL(this.repository.endpoint)
    const href = `${baseHref}/orgs/${org}/teams/${team}`
    const anchor = document.createElement('a')
    anchor.textContent = `@${org}/${team}`
    anchor.href = href
    return anchor
  }
```

**File:** app/src/ui/notifications/pull-request-comment-like.tsx (L162-164)
```typescript
  private onMarkdownLinkClicked = (url: string) => {
    this.props.dispatcher.openInBrowser(url)
  }
```

**File:** app/src/ui/lib/sandboxed-markdown.tsx (L292-305)
```typescript
  private setupLinkInterceptor(doc: Document): void {
    doc.addEventListener('click', ev => {
      if (doc.defaultView && ev.target instanceof doc.defaultView.Element) {
        const a = ev.target.closest('a')
        if (a !== null) {
          ev.preventDefault()

          if (/^https?:/.test(a.protocol)) {
            this.props.onMarkdownLinkClicked?.(a.href)
          }
        }
      }
    })
  }
```
