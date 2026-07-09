# Q2523: Starknet normalize decimals helper upgraded token changes supply semantics under live bridge mappings at boundary values

## Question
Can an unprivileged attacker trigger `public deploy path through `deploy_token`` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `starknet/src/omni_bridge.cairo::_normalizeDecimals` violate `decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs` in the `upgraded token changes supply semantics under live bridge mappings` attack class because caps decimals to Starknet’s maximum supported value before bridge-token deployment becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_normalizeDecimals`
- Entrypoint: `public deploy path through `deploy_token``
- Attacker controls: declared remote decimals and all downstream amount conversions that assume the capped value
- Exploit idea: Even when upgrade is admin-gated, test whether public flows remain safe if token semantics drift across versions. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Diff pre/post-upgrade token behavior under the same public bridge flow and assert invariant preservation for supply, callbacks, and owner checks. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
