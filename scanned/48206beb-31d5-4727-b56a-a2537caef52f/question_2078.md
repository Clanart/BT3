# Q2078: NEAR migrated token map lookup origin inference changes custody branch

## Question
Can an unprivileged attacker choose a token through `public `ft_on_transfer` migration branch plus DAO-created migration state` such that `near/omni-bridge/src/lib.rs::get_migrated_token and migrate flow interaction` infers the wrong origin chain from naming, caches, or config, violating `migration lookup must not let a user force a stale or wrong token mapping into an otherwise valid transfer path`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_migrated_token and migrate flow interaction`
- Entrypoint: `public `ft_on_transfer` migration branch plus DAO-created migration state`
- Attacker controls: old token choice, transfer amount, and downstream swap timing
- Exploit idea: Probe naming-convention inference and cache invalidation around deployed or migrated tokens.
- Invariant to test: migration lookup must not let a user force a stale or wrong token mapping into an otherwise valid transfer path
- Expected Immunefi impact: Balance manipulation
- Fast validation: Generate tokens near every naming boundary and assert that origin inference matches the canonical mapping and custody model.
