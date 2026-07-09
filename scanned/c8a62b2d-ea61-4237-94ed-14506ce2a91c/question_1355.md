# Q1355: NEAR bind_token entry same remote asset deployable via multiple proof paths

## Question
Can an unprivileged attacker use `public `bind_token` proof-submission flow` to deploy or bind the same remote asset through a second path because `near/omni-bridge/src/lib.rs::bind_token` authenticates verifies a deploy-token proof, writes token mappings in `bind_token_callback`, then refunds leftover deposit in a second callback differently than another deploy path, violating `binding an existing Near token to a foreign asset must remain one-to-one and fully collateral-accounted across proof replay, partial failure, and refund paths`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token`
- Entrypoint: `public `bind_token` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and timing versus token deployment
- Exploit idea: Compare metadata-based deployment, deploy-token binding, native-token deployment, and chain-specific extension paths.
- Invariant to test: binding an existing Near token to a foreign asset must remain one-to-one and fully collateral-accounted across proof replay, partial failure, and refund paths
- Expected Immunefi impact: Balance manipulation
- Fast validation: Attempt the same remote asset through every supported path and assert that the bridge converges to one canonical local representation.
