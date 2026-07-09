# Q3254: Starknet normalize decimals helper global asset-conservation invariant break

## Question
Can an unprivileged attacker combine the public surface behind `public deploy path through `deploy_token`` with the code paths summarized by `starknet/src/omni_bridge.cairo::_normalizeDecimals` and make total redeemable claims across chains exceed the total burned, locked, or custodied assets tracked by caps decimals to Starknet’s maximum supported value before bridge-token deployment, violating `decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_normalizeDecimals`
- Entrypoint: `public deploy path through `deploy_token``
- Attacker controls: declared remote decimals and all downstream amount conversions that assume the capped value
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class.
- Invariant to test: decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step.
