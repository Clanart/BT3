# Q2836: NEAR per-chain token origin detection origin inference changes custody branch via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public init/finalize/lock flows through token-origin checks` and then replay or reorder the adjacent bridge step that consumes the same state so that `near/omni-bridge/src/lib.rs::get_token_origin_chain` ends up accepting two inconsistent interpretations of the same economic event specifically around `origin inference changes custody branch` under infers origin chain from deployment caches, UTXO config, or token-account naming conventions before deciding whether to lock/unlock liquidity, violating `origin-chain inference must never let an attacker make the bridge classify a token as native when it is wrapped, or vice versa, because that changes custody accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_token_origin_chain`
- Entrypoint: `public init/finalize/lock flows through token-origin checks`
- Attacker controls: token account ids whose prefixes influence inferred origin chain and whether the token is in deployed sets or UTXO configs
- Exploit idea: Probe naming-convention inference and cache invalidation around deployed or migrated tokens. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: origin-chain inference must never let an attacker make the bridge classify a token as native when it is wrapped, or vice versa, because that changes custody accounting
- Expected Immunefi impact: Balance manipulation
- Fast validation: Generate tokens near every naming boundary and assert that origin inference matches the canonical mapping and custody model. Then replay or reorder the adjacent bridge step that consumes the same state and assert that the bridge still exposes only one valid economic outcome.
