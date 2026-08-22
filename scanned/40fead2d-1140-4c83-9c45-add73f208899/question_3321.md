# Q3321: getGenericPassword: repository-to-account / endpoint confusion

## Question
Can an attacker-crafted remote URL or repository owner/name reaching `getGenericPassword` in [app/src/lib/trampoline/find-account.ts] collide with another endpoint/account in matching, so Desktop attaches the wrong account's token or private data to the request?

## Target
- File/function: [app/src/lib/trampoline/find-account.ts] — `getGenericPassword`
- Entrypoint: Repository matching, endpoint/account selection, token-store keys, or cached user/PR data
- Attacker controls: remote URL, repository owner/name, endpoint host, cached API identifiers
- Exploit idea: Can an attacker-crafted remote URL or repository owner/name reaching `getGenericPassword` in [app/src/lib/trampoline/find-account.ts] collide with another endpoint/account in matching, so Desktop attaches the wrong account's token or private data to the request?
- Invariant to test: credentials and cached private data are only ever associated with the exact repository owner and endpoint they belong to
- Expected Immunefi impact: High - one account's credential or private data is attached to a repository or request belonging to a different owner/endpoint (target scope: "High. Repository-to-account confusion in repository matching, endpoint or account selection, token-store keys, or cached...")
- Fast validation: Give `getGenericPassword` a lookalike/ambiguous remote or endpoint in a test and assert it selects the correct account/token, not a mismatched one
