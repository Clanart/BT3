# Q2613: NEAR process_fin_transfer_to_other_chain final settlement and later fee claim can diverge

## Question
Can an unprivileged attacker drive `internal path reached from public `fin_transfer` for non-Near recipients` so that `near/omni-bridge/src/lib.rs::process_fin_transfer_to_other_chain` settles principal under one interpretation of amount or transfer id while fee claim later uses another because of marks the transfer finalised, unlocks origin-side liquidity, re-locks destination-side fee or amount, optionally sends the fast-transfer payout to a relayer, or stores a new pending transfer for the next chain, violating `cross-chain forwarding must never let one verified inbound event release value locally and also create a second valid outbound claim with inconsistent lock accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::process_fin_transfer_to_other_chain`
- Entrypoint: `internal path reached from public `fin_transfer` for non-Near recipients`
- Attacker controls: recipient chain, predecessor account, transfer message, fast-transfer status, and token origin chain
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution.
- Invariant to test: cross-chain forwarding must never let one verified inbound event release value locally and also create a second valid outbound claim with inconsistent lock accounting
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event.
