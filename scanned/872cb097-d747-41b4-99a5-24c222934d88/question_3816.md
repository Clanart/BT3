# Q3816: Starknet old/new metadata ABI detection Starknet metadata ABI split changes remote asset identity

## Question
Can an unprivileged attacker choose a token and call `public Starknet `log_metadata`` so that `starknet/src/omni_bridge.cairo::log_metadata low-level ABI branching` interprets the same metadata call under the wrong ABI family, violating `ABI detection must not let a malicious token return shape-shifted metadata that downstream chains interpret as another asset identity`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata low-level ABI branching`
- Entrypoint: `public Starknet `log_metadata``
- Attacker controls: token contract that can return `name`/`symbol` as one felt, many felts, or malformed ABI-encoded data
- Exploit idea: Exploit mixed old-style felt and new-style ByteArray return conventions from arbitrary token contracts.
- Invariant to test: ABI detection must not let a malicious token return shape-shifted metadata that downstream chains interpret as another asset identity
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Return ambiguous metadata payloads and assert that the bridge rejects or canonically normalizes them before remote deployment.
