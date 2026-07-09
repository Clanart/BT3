# Q1910: Starknet normalize decimals helper fake bridge-controlled token accepted as canonical at boundary values

## Question
Can an unprivileged attacker trigger `public deploy path through `deploy_token`` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `starknet/src/omni_bridge.cairo::_normalizeDecimals` violate `decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs` in the `fake bridge-controlled token accepted as canonical` attack class because caps decimals to Starknet’s maximum supported value before bridge-token deployment becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_normalizeDecimals`
- Entrypoint: `public deploy path through `deploy_token``
- Attacker controls: declared remote decimals and all downstream amount conversions that assume the capped value
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
