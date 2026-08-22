# Q4817: upsertGitHubRepositoryLight: repository-to-account / endpoint confusion

## Question
Does `upsertGitHubRepositoryLight` in [app/src/lib/stores/repositories-store.ts] key credentials or cached user/PR data by a value an attacker can spoof (lookalike host, case/normalization gap), letting one owner's data bind to an attacker repository?

## Target
- File/function: [app/src/lib/stores/repositories-store.ts] — `upsertGitHubRepositoryLight`
- Entrypoint: Repository matching, endpoint/account selection, token-store keys, or cached user/PR data
- Attacker controls: remote URL, repository owner/name, endpoint host, cached API identifiers
- Exploit idea: Does `upsertGitHubRepositoryLight` in [app/src/lib/stores/repositories-store.ts] key credentials or cached user/PR data by a value an attacker can spoof (lookalike host, case/normalization gap), letting one owner's data bind to an attacker repository?
- Invariant to test: credentials and cached private data are only ever associated with the exact repository owner and endpoint they belong to
- Expected Immunefi impact: High - one account's credential or private data is attached to a repository or request belonging to a different owner/endpoint (target scope: "High. Repository-to-account confusion in repository matching, endpoint or account selection, token-store keys, or cached...")
- Fast validation: Give `upsertGitHubRepositoryLight` a lookalike/ambiguous remote or endpoint in a test and assert it selects the correct account/token, not a mismatched one
