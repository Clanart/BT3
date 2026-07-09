# Q2005: NEAR process_fin_transfer_to_other_chain unlock or relock asymmetry

## Question
Can an unprivileged attacker make `near/omni-bridge/src/lib.rs::process_fin_transfer_to_other_chain` unlock, relock, or revert lock state inconsistently during `internal path reached from public `fin_transfer` for non-Near recipients` because of marks the transfer finalised, unlocks origin-side liquidity, re-locks destination-side fee or amount, optionally sends the fast-transfer payout to a relayer, or stores a new pending transfer for the next chain, violating `cross-chain forwarding must never let one verified inbound event release value locally and also create a second valid outbound claim with inconsistent lock accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::process_fin_transfer_to_other_chain`
- Entrypoint: `internal path reached from public `fin_transfer` for non-Near recipients`
- Attacker controls: recipient chain, predecessor account, transfer message, fast-transfer status, and token origin chain
- Exploit idea: Look for one branch that unlocks origin liquidity while another branch also mints or stores a second claim.
- Invariant to test: cross-chain forwarding must never let one verified inbound event release value locally and also create a second valid outbound claim with inconsistent lock accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model successful and failed delivery plus fast-transfer branches and assert that aggregate locked liquidity matches outstanding claims after each path.
