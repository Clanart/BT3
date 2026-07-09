# Q3786: Starknet log_metadata Starknet metadata ABI split changes remote asset identity

## Question
Can an unprivileged attacker choose a token and call `public Starknet metadata logging entrypoint` so that `starknet/src/omni_bridge.cairo::log_metadata` interprets the same metadata call under the wrong ABI family, violating `metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata`
- Entrypoint: `public Starknet metadata logging entrypoint`
- Attacker controls: token address and the token’s reported `name`, `symbol`, and `decimals` ABI behavior
- Exploit idea: Exploit mixed old-style felt and new-style ByteArray return conventions from arbitrary token contracts.
- Invariant to test: metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Return ambiguous metadata payloads and assert that the bridge rejects or canonically normalizes them before remote deployment.
