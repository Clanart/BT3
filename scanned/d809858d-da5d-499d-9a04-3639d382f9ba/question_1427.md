# Q1427: Starknet normalize decimals helper fake bridge-controlled token accepted as canonical

## Question
Can an unprivileged attacker use `public deploy path through `deploy_token`` to register or settle against a token that only looks bridge-controlled because `starknet/src/omni_bridge.cairo::_normalizeDecimals` relies on caps decimals to Starknet’s maximum supported value before bridge-token deployment, violating `decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_normalizeDecimals`
- Entrypoint: `public deploy path through `deploy_token``
- Attacker controls: declared remote decimals and all downstream amount conversions that assume the capped value
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity.
- Invariant to test: decimal normalization must not let a wrapped token represent more or less claimable value than the source asset actually backs
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset.
