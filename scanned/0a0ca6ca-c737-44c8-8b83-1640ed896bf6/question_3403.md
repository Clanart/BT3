# Q3403: NEAR per-chain token origin detection locked balance diverges from actual locked asset via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public init/finalize/lock flows through token-origin checks` and then replay or reorder the adjacent bridge step that consumes the same state so that `near/omni-bridge/src/lib.rs::get_token_origin_chain` ends up accepting two inconsistent interpretations of the same economic event specifically around `locked balance diverges from actual locked asset` under infers origin chain from deployment caches, UTXO config, or token-account naming conventions before deciding whether to lock/unlock liquidity, violating `origin-chain inference must never let an attacker make the bridge classify a token as native when it is wrapped, or vice versa, because that changes custody accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_token_origin_chain`
- Entrypoint: `public init/finalize/lock flows through token-origin checks`
- Attacker controls: token account ids whose prefixes influence inferred origin chain and whether the token is in deployed sets or UTXO configs
- Exploit idea: Target lock/unlock helpers around failed callbacks, cross-chain forwarding, and fast transfers. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: origin-chain inference must never let an attacker make the bridge classify a token as native when it is wrapped, or vice versa, because that changes custody accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Track real token/ETH/SOL custody alongside lock rows and assert exact equality of outstanding obligations and locked liquidity. Then replay or reorder the adjacent bridge step that consumes the same state and assert that the bridge still exposes only one valid economic outcome.
