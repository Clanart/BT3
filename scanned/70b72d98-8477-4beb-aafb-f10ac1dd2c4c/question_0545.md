# Q545: NEAR get_locked_tokens custody accounting diverges from wrapped supply at boundary values

## Question
Can an unprivileged attacker trigger `public lock-accounting view used by bridge operators and relayers` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/token_lock.rs::get_locked_tokens` violate `every observed lock amount must stay synchronized with actual bridge custody and pending outbound obligations` in the `custody accounting diverges from wrapped supply` attack class because reads the `locked_tokens` table that tracks bridge liquidity locked on behalf of foreign-chain claims becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::get_locked_tokens`
- Entrypoint: `public lock-accounting view used by bridge operators and relayers`
- Attacker controls: chain kind and token id chosen by the caller
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: every observed lock amount must stay synchronized with actual bridge custody and pending outbound obligations
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
