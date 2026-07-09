# Q283: NEAR per-chain token origin detection address alias collapses distinct bridge subjects via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public init/finalize/lock flows through token-origin checks` and then replay or reorder the adjacent bridge step that consumes the same state so that `near/omni-bridge/src/lib.rs::get_token_origin_chain` ends up accepting two inconsistent interpretations of the same economic event specifically around `address alias collapses distinct bridge subjects` under infers origin chain from deployment caches, UTXO config, or token-account naming conventions before deciding whether to lock/unlock liquidity, violating `origin-chain inference must never let an attacker make the bridge classify a token as native when it is wrapped, or vice versa, because that changes custody accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_token_origin_chain`
- Entrypoint: `public init/finalize/lock flows through token-origin checks`
- Attacker controls: token account ids whose prefixes influence inferred origin chain and whether the token is in deployed sets or UTXO configs
- Exploit idea: Attack mixed-case hex, leading-zero, base58, or padded-address forms across chain adapters. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: origin-chain inference must never let an attacker make the bridge classify a token as native when it is wrapped, or vice versa, because that changes custody accounting
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip equivalent-looking addresses through every parser and assert injective mapping into local bridge identities. Then replay or reorder the adjacent bridge step that consumes the same state and assert that the bridge still exposes only one valid economic outcome.
