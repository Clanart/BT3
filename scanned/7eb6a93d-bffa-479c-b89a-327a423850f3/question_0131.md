# Q131: Starknet old/new metadata ABI detection malicious metadata manufactures a bridge identity

## Question
Can an unprivileged attacker invoke `public Starknet `log_metadata`` with a malicious token or metadata payload so that `starknet/src/omni_bridge.cairo::log_metadata low-level ABI branching` records a deceptive asset identity that later drives deployment or claims, violating `ABI detection must not let a malicious token return shape-shifted metadata that downstream chains interpret as another asset identity`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata low-level ABI branching`
- Entrypoint: `public Starknet `log_metadata``
- Attacker controls: token contract that can return `name`/`symbol` as one felt, many felts, or malformed ABI-encoded data
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs.
- Invariant to test: ABI detection must not let a malicious token return shape-shifted metadata that downstream chains interpret as another asset identity
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals.
