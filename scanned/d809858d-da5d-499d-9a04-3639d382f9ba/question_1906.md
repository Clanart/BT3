# Q1906: Starknet fin_transfer recipient or fee-recipient rebinding at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet settlement entrypoint` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `starknet/src/omni_bridge.cairo::fin_transfer` violate `a signed inbound settlement must never be replayable, branch-switchable, or capable of failing after finalisation state changes in a way that strands or duplicates funds` in the `recipient or fee-recipient rebinding` attack class because checks pause flags, enforces `!is_transfer_finalised(destination_nonce)`, marks the nonce finalised, verifies the signed Borsh payload, and then releases native or bridge-token value becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::fin_transfer`
- Entrypoint: `public Starknet settlement entrypoint`
- Attacker controls: signature fields, destination nonce, origin chain, origin nonce, token address, amount, recipient, fee recipient, and message
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: a signed inbound settlement must never be replayable, branch-switchable, or capable of failing after finalisation state changes in a way that strands or duplicates funds
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
