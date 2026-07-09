# Q1092: Starknet log_metadata native versus wrapped registration confusion through cross-module drift

## Question
Can an unprivileged attacker use `public Starknet metadata logging entrypoint` with control over token address and the token’s reported `name`, `symbol`, and `decimals` ABI behavior and desynchronize `starknet/src/omni_bridge.cairo::log_metadata` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `native versus wrapped registration confusion` attack class because performs low-level calls to infer whether metadata fields are returned as `felt252` or `ByteArray`, then emits a `LogMetadata` event, violating `metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata`
- Entrypoint: `public Starknet metadata logging entrypoint`
- Attacker controls: token address and the token’s reported `name`, `symbol`, and `decimals` ABI behavior
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::log_metadata` and the adjacent mint, burn, or custody accounting after every branch.
