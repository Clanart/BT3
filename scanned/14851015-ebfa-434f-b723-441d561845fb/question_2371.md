# Q2371: Starknet normalize decimals helper upgraded token changes supply semantics under live bridge mappings through cross-module drift

## Question
Can an unprivileged attacker use `public deploy path through `deploy_token`` with control over declared remote decimals and all downstream amount conversions that assume the capped value and desynchronize `starknet/src/omni_bridge.cairo::_normalizeDecimals` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `upgraded token changes supply semantics under live bridge mappings` attack class because caps decimals to Starknet’s maximum supported value before bridge-token deployment, violating `decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_normalizeDecimals`
- Entrypoint: `public deploy path through `deploy_token``
- Attacker controls: declared remote decimals and all downstream amount conversions that assume the capped value
- Exploit idea: Even when upgrade is admin-gated, test whether public flows remain safe if token semantics drift across versions. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Diff pre/post-upgrade token behavior under the same public bridge flow and assert invariant preservation for supply, callbacks, and owner checks. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::_normalizeDecimals` and the adjacent mint, burn, or custody accounting after every branch.
