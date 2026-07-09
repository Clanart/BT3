# Q432: Starknet normalize decimals helper decimal cap creates wrong economic model through cross-module drift

## Question
Can an unprivileged attacker use `public deploy path through `deploy_token`` with control over declared remote decimals and all downstream amount conversions that assume the capped value and desynchronize `starknet/src/omni_bridge.cairo::_normalizeDecimals` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `decimal cap creates wrong economic model` attack class because caps decimals to Starknet’s maximum supported value before bridge-token deployment, violating `decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_normalizeDecimals`
- Entrypoint: `public deploy path through `deploy_token``
- Attacker controls: declared remote decimals and all downstream amount conversions that assume the capped value
- Exploit idea: Target capped decimals on EVM, Solana, and Starknet deployments and later amount conversions during sign/finalize/claim flows. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs
- Expected Immunefi impact: Balance manipulation
- Fast validation: Deploy high-decimal assets and assert that every later amount conversion preserves one consistent economic relation to the source asset. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::_normalizeDecimals` and the adjacent mint, burn, or custody accounting after every branch.
