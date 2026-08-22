# Q5188: renderTimelineItem: repository-to-account / endpoint confusion

## Question
Can a lookalike or ambiguous endpoint host handled by `renderTimelineItem` in [app/src/ui/notifications/pull-request-comment-like.tsx] cause Desktop to select the dot-com account for an Enterprise repo (or vice versa) and leak the token there?

## Target
- File/function: [app/src/ui/notifications/pull-request-comment-like.tsx] — `renderTimelineItem`
- Entrypoint: Repository matching, endpoint/account selection, token-store keys, or cached user/PR data
- Attacker controls: remote URL, repository owner/name, endpoint host, cached API identifiers
- Exploit idea: Can a lookalike or ambiguous endpoint host handled by `renderTimelineItem` in [app/src/ui/notifications/pull-request-comment-like.tsx] cause Desktop to select the dot-com account for an Enterprise repo (or vice versa) and leak the token there?
- Invariant to test: credentials and cached private data are only ever associated with the exact repository owner and endpoint they belong to
- Expected Immunefi impact: High - one account's credential or private data is attached to a repository or request belonging to a different owner/endpoint (target scope: "High. Repository-to-account confusion in repository matching, endpoint or account selection, token-store keys, or cached...")
- Fast validation: Give `renderTimelineItem` a lookalike/ambiguous remote or endpoint in a test and assert it selects the correct account/token, not a mismatched one
