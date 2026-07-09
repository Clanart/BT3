# Q473: NEAR UTXO connector withdrawal coupling UTXO native-token requirement bypass through cross-module drift

## Question
Can an unprivileged attacker use `public UTXO-origin forward path plus downstream connector use` with control over UTXO transfer id, output set, relayer fee, and destination-chain token assumptions and desynchronize `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain and btc.rs` from the adjacent the next module that consumes the same asset or transfer id that shares the same asset, nonce, proof subject, or mapping specifically in the `UTXO native-token requirement bypass` attack class because recomposes a UTXO-origin transfer into another bridge leg and eventually interacts with chain-specific UTXO connector behavior, violating `UTXO-origin state must not let one spendable output drive both a direct payout and a second connector withdrawal obligation`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain and btc.rs`
- Entrypoint: `public UTXO-origin forward path plus downstream connector use`
- Attacker controls: UTXO transfer id, output set, relayer fee, and destination-chain token assumptions
- Exploit idea: Target token-origin checks and chain-specific native-token requirements in BTC/Zcash-style flows. Focus on drift between this module and the adjacent the next module that consumes the same asset or transfer id.
- Invariant to test: UTXO-origin state must not let one spendable output drive both a direct payout and a second connector withdrawal obligation
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz chain/token combinations and assert that every accepted UTXO-facing flow uses exactly the configured native asset for that chain. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain and btc.rs` and the adjacent the next module that consumes the same asset or transfer id after every branch.
