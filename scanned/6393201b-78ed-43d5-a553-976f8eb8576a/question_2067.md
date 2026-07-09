# Q2067: Starknet normalize decimals helper upgraded token changes supply semantics under live bridge mappings

## Question
Can an unprivileged attacker leverage public bridge flows after `public deploy path through `deploy_token`` so that `starknet/src/omni_bridge.cairo::_normalizeDecimals` interacts with an upgraded token whose mint/burn semantics no longer match prior assumptions, violating `decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_normalizeDecimals`
- Entrypoint: `public deploy path through `deploy_token``
- Attacker controls: declared remote decimals and all downstream amount conversions that assume the capped value
- Exploit idea: Even when upgrade is admin-gated, test whether public flows remain safe if token semantics drift across versions.
- Invariant to test: decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Diff pre/post-upgrade token behavior under the same public bridge flow and assert invariant preservation for supply, callbacks, and owner checks.
