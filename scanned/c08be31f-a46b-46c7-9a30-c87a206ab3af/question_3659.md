# Q3659: Starknet normalize decimals helper global asset-conservation invariant break at boundary values

## Question
Can an unprivileged attacker trigger `public deploy path through `deploy_token`` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `starknet/src/omni_bridge.cairo::_normalizeDecimals` violate `decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs` in the `global asset-conservation invariant break` attack class because caps decimals to Starknet’s maximum supported value before bridge-token deployment becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_normalizeDecimals`
- Entrypoint: `public deploy path through `deploy_token``
- Attacker controls: declared remote decimals and all downstream amount conversions that assume the capped value
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
