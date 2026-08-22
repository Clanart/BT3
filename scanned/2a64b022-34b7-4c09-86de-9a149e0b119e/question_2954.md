# Q2954: clone: repository-to-account / endpoint confusion

## Question
Can cached pull-request/user records matched by `clone` in [app/src/lib/stores/cloning-repositories-store.ts] be confused across owners, exposing one account's private repository data in the context of an attacker-controlled repository?

## Target
- File/function: [app/src/lib/stores/cloning-repositories-store.ts] — `clone`
- Entrypoint: Repository matching, endpoint/account selection, token-store keys, or cached user/PR data
- Attacker controls: remote URL, repository owner/name, endpoint host, cached API identifiers
- Exploit idea: Can cached pull-request/user records matched by `clone` in [app/src/lib/stores/cloning-repositories-store.ts] be confused across owners, exposing one account's private repository data in the context of an attacker-controlled repository?
- Invariant to test: credentials and cached private data are only ever associated with the exact repository owner and endpoint they belong to
- Expected Immunefi impact: High - one account's credential or private data is attached to a repository or request belonging to a different owner/endpoint (target scope: "High. Repository-to-account confusion in repository matching, endpoint or account selection, token-store keys, or cached...")
- Fast validation: Give `clone` a lookalike/ambiguous remote or endpoint in a test and assert it selects the correct account/token, not a mismatched one
