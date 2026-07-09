# Q1099: Starknet normalize decimals helper native versus wrapped registration confusion through cross-module drift

## Question
Can an unprivileged attacker use `public deploy path through `deploy_token`` with control over declared remote decimals and all downstream amount conversions that assume the capped value and desynchronize `starknet/src/omni_bridge.cairo::_normalizeDecimals` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `native versus wrapped registration confusion` attack class because caps decimals to Starknet’s maximum supported value before bridge-token deployment, violating `decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_normalizeDecimals`
- Entrypoint: `public deploy path through `deploy_token``
- Attacker controls: declared remote decimals and all downstream amount conversions that assume the capped value
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::_normalizeDecimals` and the adjacent mint, burn, or custody accounting after every branch.
