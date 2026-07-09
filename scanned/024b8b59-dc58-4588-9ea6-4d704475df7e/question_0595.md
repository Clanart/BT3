# Q595: Starknet fin_transfer replay guard can be bypassed or consumed incorrectly at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet settlement entrypoint` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `starknet/src/omni_bridge.cairo::fin_transfer` violate `a signed inbound settlement must never be replayable, branch-switchable, or capable of failing after finalisation state changes in a way that strands or duplicates funds` in the `replay guard can be bypassed or consumed incorrectly` attack class because checks pause flags, enforces `!is_transfer_finalised(destination_nonce)`, marks the nonce finalised, verifies the signed Borsh payload, and then releases native or bridge-token value becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::fin_transfer`
- Entrypoint: `public Starknet settlement entrypoint`
- Attacker controls: signature fields, destination nonce, origin chain, origin nonce, token address, amount, recipient, fee recipient, and message
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: a signed inbound settlement must never be replayable, branch-switchable, or capable of failing after finalisation state changes in a way that strands or duplicates funds
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
