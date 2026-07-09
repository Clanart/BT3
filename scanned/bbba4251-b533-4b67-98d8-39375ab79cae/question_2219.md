# Q2219: Starknet normalize decimals helper upgraded token changes supply semantics under live bridge mappings via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public deploy path through `deploy_token`` and then replay or reorder later bind, deploy, or metadata-consumption step so that `starknet/src/omni_bridge.cairo::_normalizeDecimals` ends up accepting two inconsistent interpretations of the same economic event specifically around `upgraded token changes supply semantics under live bridge mappings` under caps decimals to Starknet’s maximum supported value before bridge-token deployment, violating `decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_normalizeDecimals`
- Entrypoint: `public deploy path through `deploy_token``
- Attacker controls: declared remote decimals and all downstream amount conversions that assume the capped value
- Exploit idea: Even when upgrade is admin-gated, test whether public flows remain safe if token semantics drift across versions. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Diff pre/post-upgrade token behavior under the same public bridge flow and assert invariant preservation for supply, callbacks, and owner checks. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
