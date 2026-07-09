# Q3471: NEAR process_fin_transfer_to_other_chain fast path and normal path can both pay through cross-module drift

## Question
Can an unprivileged attacker use `internal path reached from public `fin_transfer` for non-Near recipients` with control over recipient chain, predecessor account, transfer message, fast-transfer status, and token origin chain and desynchronize `near/omni-bridge/src/lib.rs::process_fin_transfer_to_other_chain` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `fast path and normal path can both pay` attack class because marks the transfer finalised, unlocks origin-side liquidity, re-locks destination-side fee or amount, optionally sends the fast-transfer payout to a relayer, or stores a new pending transfer for the next chain, violating `cross-chain forwarding must never let one verified inbound event release value locally and also create a second valid outbound claim with inconsistent lock accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::process_fin_transfer_to_other_chain`
- Entrypoint: `internal path reached from public `fin_transfer` for non-Near recipients`
- Attacker controls: recipient chain, predecessor account, transfer message, fast-transfer status, and token origin chain
- Exploit idea: Target relayer substitution, `origin_transfer_id`, and the moment when fast transfers become finalised or removable. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: cross-chain forwarding must never let one verified inbound event release value locally and also create a second valid outbound claim with inconsistent lock accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate fast settlement before and after the canonical proof arrives and assert that total user-plus-relayer payouts never exceed the original transfer amount plus intended fee split. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::process_fin_transfer_to_other_chain` and the adjacent replay-protection bookkeeping after every branch.
