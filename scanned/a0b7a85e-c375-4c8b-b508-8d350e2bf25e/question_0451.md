# Q451: NEAR per-chain token origin detection address alias collapses distinct bridge subjects through cross-module drift

## Question
Can an unprivileged attacker use `public init/finalize/lock flows through token-origin checks` with control over token account ids whose prefixes influence inferred origin chain and whether the token is in deployed sets or UTXO configs and desynchronize `near/omni-bridge/src/lib.rs::get_token_origin_chain` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `address alias collapses distinct bridge subjects` attack class because infers origin chain from deployment caches, UTXO config, or token-account naming conventions before deciding whether to lock/unlock liquidity, violating `origin-chain inference must never let an attacker make the bridge classify a token as native when it is wrapped, or vice versa, because that changes custody accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_token_origin_chain`
- Entrypoint: `public init/finalize/lock flows through token-origin checks`
- Attacker controls: token account ids whose prefixes influence inferred origin chain and whether the token is in deployed sets or UTXO configs
- Exploit idea: Attack mixed-case hex, leading-zero, base58, or padded-address forms across chain adapters. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: origin-chain inference must never let an attacker make the bridge classify a token as native when it is wrapped, or vice versa, because that changes custody accounting
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip equivalent-looking addresses through every parser and assert injective mapping into local bridge identities. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::get_token_origin_chain` and the adjacent token-mapping and asset-identity logic after every branch.
