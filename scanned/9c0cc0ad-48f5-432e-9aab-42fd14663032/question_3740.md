# Q3740: NEAR process_fin_transfer_to_near fast path and normal path can both pay

## Question
Can an unprivileged attacker use `internal path reached from public `fin_transfer`` to make the fast path and the eventual normal settlement each believe they are the sole payer because `near/omni-bridge/src/lib.rs::process_fin_transfer_to_near` relies on marks the transfer finalised, optionally redirects payout to the fast-transfer relayer, checks storage-deposit actions for recipient and fee recipients, unlocks tokens, sends tokens, and mints fee tokens in the callback, violating `Near-side finalization must never misroute recipient funds, fee funds, or lock state across storage setup, fast-transfer substitution, and callback resolution`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::process_fin_transfer_to_near`
- Entrypoint: `internal path reached from public `fin_transfer``
- Attacker controls: recipient account, predecessor account, transfer message, storage-deposit actions, fast-transfer status, and lock actions
- Exploit idea: Target relayer substitution, `origin_transfer_id`, and the moment when fast transfers become finalised or removable.
- Invariant to test: Near-side finalization must never misroute recipient funds, fee funds, or lock state across storage setup, fast-transfer substitution, and callback resolution
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate fast settlement before and after the canonical proof arrives and assert that total user-plus-relayer payouts never exceed the original transfer amount plus intended fee split.
