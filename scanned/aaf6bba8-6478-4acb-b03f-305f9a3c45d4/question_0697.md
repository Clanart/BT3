# Q697: NEAR process_fin_transfer_to_other_chain recipient or fee-recipient rebinding

## Question
Can an unprivileged attacker submit data through `internal path reached from public `fin_transfer` for non-Near recipients` that makes `near/omni-bridge/src/lib.rs::process_fin_transfer_to_other_chain` settle principal to one party but authorize fee claim or callback routing for another due to marks the transfer finalised, unlocks origin-side liquidity, re-locks destination-side fee or amount, optionally sends the fast-transfer payout to a relayer, or stores a new pending transfer for the next chain, violating `cross-chain forwarding must never let one verified inbound event release value locally and also create a second valid outbound claim with inconsistent lock accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::process_fin_transfer_to_other_chain`
- Entrypoint: `internal path reached from public `fin_transfer` for non-Near recipients`
- Attacker controls: recipient chain, predecessor account, transfer message, fast-transfer status, and token origin chain
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities.
- Invariant to test: cross-chain forwarding must never let one verified inbound event release value locally and also create a second valid outbound claim with inconsistent lock accounting
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple.
