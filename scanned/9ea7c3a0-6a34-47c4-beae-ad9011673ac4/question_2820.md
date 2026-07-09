# Q2820: Starknet normalize decimals helper custody accounting diverges from wrapped supply via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public deploy path through `deploy_token`` and then replay or reorder later bind, deploy, or metadata-consumption step so that `starknet/src/omni_bridge.cairo::_normalizeDecimals` ends up accepting two inconsistent interpretations of the same economic event specifically around `custody accounting diverges from wrapped supply` under caps decimals to Starknet’s maximum supported value before bridge-token deployment, violating `decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_normalizeDecimals`
- Entrypoint: `public deploy path through `deploy_token``
- Attacker controls: declared remote decimals and all downstream amount conversions that assume the capped value
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
