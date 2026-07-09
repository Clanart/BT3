# Q3111: Starknet fin_transfer final settlement and later fee claim can diverge at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet settlement entrypoint` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `starknet/src/omni_bridge.cairo::fin_transfer` violate `a signed inbound settlement must never be replayable, branch-switchable, or capable of failing after finalisation state changes in a way that strands or duplicates funds` in the `final settlement and later fee claim can diverge` attack class because checks pause flags, enforces `!is_transfer_finalised(destination_nonce)`, marks the nonce finalised, verifies the signed Borsh payload, and then releases native or bridge-token value becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::fin_transfer`
- Entrypoint: `public Starknet settlement entrypoint`
- Attacker controls: signature fields, destination nonce, origin chain, origin nonce, token address, amount, recipient, fee recipient, and message
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: a signed inbound settlement must never be replayable, branch-switchable, or capable of failing after finalisation state changes in a way that strands or duplicates funds
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
