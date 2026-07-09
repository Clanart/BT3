# Q760: Starknet log_metadata native versus wrapped registration confusion

## Question
Can an unprivileged attacker reach `public Starknet metadata logging entrypoint` and make `starknet/src/omni_bridge.cairo::log_metadata` treat a wrapped asset as native or a native asset as wrapped because of performs low-level calls to infer whether metadata fields are returned as `felt252` or `ByteArray`, then emits a `LogMetadata` event, violating `metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata`
- Entrypoint: `public Starknet metadata logging entrypoint`
- Attacker controls: token address and the token’s reported `name`, `symbol`, and `decimals` ABI behavior
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration.
- Invariant to test: metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model.
