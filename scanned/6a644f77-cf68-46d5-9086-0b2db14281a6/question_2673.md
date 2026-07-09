# Q2673: Starknet normalize decimals helper custody accounting diverges from wrapped supply

## Question
Can an unprivileged attacker use `public deploy path through `deploy_token`` to make `starknet/src/omni_bridge.cairo::_normalizeDecimals` increase wrapped supply or reduce custody without the complementary change on the other side, violating `decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_normalizeDecimals`
- Entrypoint: `public deploy path through `deploy_token``
- Attacker controls: declared remote decimals and all downstream amount conversions that assume the capped value
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value.
- Invariant to test: decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow.
