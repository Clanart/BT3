# Q1375: NEAR unlock_tokens_if_needed custody accounting diverges from wrapped supply

## Question
Can an unprivileged attacker use `internal helper reached from public finalize paths` to make `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed` increase wrapped supply or reduce custody without the complementary change on the other side, violating `unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed`
- Entrypoint: `internal helper reached from public finalize paths`
- Attacker controls: token id, chain kind interpreted as origin, and amount
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value.
- Invariant to test: unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow.
