# Q2967: Starknet normalize decimals helper custody accounting diverges from wrapped supply through cross-module drift

## Question
Can an unprivileged attacker use `public deploy path through `deploy_token`` with control over declared remote decimals and all downstream amount conversions that assume the capped value and desynchronize `starknet/src/omni_bridge.cairo::_normalizeDecimals` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `custody accounting diverges from wrapped supply` attack class because caps decimals to Starknet’s maximum supported value before bridge-token deployment, violating `decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_normalizeDecimals`
- Entrypoint: `public deploy path through `deploy_token``
- Attacker controls: declared remote decimals and all downstream amount conversions that assume the capped value
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::_normalizeDecimals` and the adjacent mint, burn, or custody accounting after every branch.
