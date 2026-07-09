# Q2460: NEAR process_fin_transfer_to_near delivery callback leaves inconsistent state at boundary values

## Question
Can an unprivileged attacker trigger `internal path reached from public `fin_transfer`` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::process_fin_transfer_to_near` violate `Near-side finalization must never misroute recipient funds, fee funds, or lock state across storage setup, fast-transfer substitution, and callback resolution` in the `delivery callback leaves inconsistent state` attack class because marks the transfer finalised, optionally redirects payout to the fast-transfer relayer, checks storage-deposit actions for recipient and fee recipients, unlocks tokens, sends tokens, and mints fee tokens in the callback becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::process_fin_transfer_to_near`
- Entrypoint: `internal path reached from public `fin_transfer``
- Attacker controls: recipient account, predecessor account, transfer message, storage-deposit actions, fast-transfer status, and lock actions
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: Near-side finalization must never misroute recipient funds, fee funds, or lock state across storage setup, fast-transfer substitution, and callback resolution
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
