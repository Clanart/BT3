# Q41: NEAR get_locked_tokens custody accounting diverges from wrapped supply

## Question
Can an unprivileged attacker use `public lock-accounting view used by bridge operators and relayers` to make `near/omni-bridge/src/token_lock.rs::get_locked_tokens` increase wrapped supply or reduce custody without the complementary change on the other side, violating `every observed lock amount must stay synchronized with actual bridge custody and pending outbound obligations`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::get_locked_tokens`
- Entrypoint: `public lock-accounting view used by bridge operators and relayers`
- Attacker controls: chain kind and token id chosen by the caller
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value.
- Invariant to test: every observed lock amount must stay synchronized with actual bridge custody and pending outbound obligations
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow.
