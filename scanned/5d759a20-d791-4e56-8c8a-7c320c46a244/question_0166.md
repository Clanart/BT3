# Q166: Starknet transfer payload Borsh encoding replay guard can be bypassed or consumed incorrectly

## Question
Can an unprivileged attacker settle through `public `fin_transfer` signature path` and make `starknet/src/bridge_types.cairo::TransferMessagePayloadTrait and related encoding` either bypass replay protection or consume it for the wrong event because of encodes Starknet transfer payload fields into Borsh bytes that are Keccak-hashed for signature verification, violating `payload encoding must stay in lockstep with Near and EVM so Starknet signatures cannot authorize a different transfer off-chain than on-chain`?

## Target
- File/function: `starknet/src/bridge_types.cairo::TransferMessagePayloadTrait and related encoding`
- Entrypoint: `public `fin_transfer` signature path`
- Attacker controls: destination nonce, origin chain, origin nonce, token address, amount, recipient, fee recipient, and message
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains.
- Invariant to test: payload encoding must stay in lockstep with Near and EVM so Starknet signatures cannot authorize a different transfer off-chain than on-chain
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used.
