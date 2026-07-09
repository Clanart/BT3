# Q1462: Starknet old/new metadata ABI detection fake bridge-controlled token accepted as canonical

## Question
Can an unprivileged attacker use `public Starknet `log_metadata`` to register or settle against a token that only looks bridge-controlled because `starknet/src/omni_bridge.cairo::log_metadata low-level ABI branching` relies on switches between old-style felt252 metadata and new-style ByteArray metadata based on return-data length from untrusted token contracts, violating `ABI detection must not let a malicious token return shape-shifted metadata that downstream chains interpret as another asset identity`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata low-level ABI branching`
- Entrypoint: `public Starknet `log_metadata``
- Attacker controls: token contract that can return `name`/`symbol` as one felt, many felts, or malformed ABI-encoded data
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity.
- Invariant to test: ABI detection must not let a malicious token return shape-shifted metadata that downstream chains interpret as another asset identity
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset.
