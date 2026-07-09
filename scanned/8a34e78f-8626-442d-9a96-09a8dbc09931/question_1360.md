# Q1360: NEAR process_fin_transfer_to_near unlock or relock asymmetry

## Question
Can an unprivileged attacker make `near/omni-bridge/src/lib.rs::process_fin_transfer_to_near` unlock, relock, or revert lock state inconsistently during `internal path reached from public `fin_transfer`` because of marks the transfer finalised, optionally redirects payout to the fast-transfer relayer, checks storage-deposit actions for recipient and fee recipients, unlocks tokens, sends tokens, and mints fee tokens in the callback, violating `Near-side finalization must never misroute recipient funds, fee funds, or lock state across storage setup, fast-transfer substitution, and callback resolution`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::process_fin_transfer_to_near`
- Entrypoint: `internal path reached from public `fin_transfer``
- Attacker controls: recipient account, predecessor account, transfer message, storage-deposit actions, fast-transfer status, and lock actions
- Exploit idea: Look for one branch that unlocks origin liquidity while another branch also mints or stores a second claim.
- Invariant to test: Near-side finalization must never misroute recipient funds, fee funds, or lock state across storage setup, fast-transfer substitution, and callback resolution
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model successful and failed delivery plus fast-transfer branches and assert that aggregate locked liquidity matches outstanding claims after each path.
