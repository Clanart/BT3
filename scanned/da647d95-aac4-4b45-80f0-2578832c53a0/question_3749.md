# Q3749: NEAR lock_tokens_if_needed custody accounting diverges from wrapped supply

## Question
Can an unprivileged attacker use `internal helper reached from public init/finalize/fast paths` to make `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed` increase wrapped supply or reduce custody without the complementary change on the other side, violating `lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed`
- Entrypoint: `internal helper reached from public init/finalize/fast paths`
- Attacker controls: token id, chain kind interpreted as destination, and amount
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value.
- Invariant to test: lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow.
