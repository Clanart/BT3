# Q360: NEAR process_fin_transfer_to_near recipient or fee-recipient rebinding through cross-module drift

## Question
Can an unprivileged attacker use `internal path reached from public `fin_transfer`` with control over recipient account, predecessor account, transfer message, storage-deposit actions, fast-transfer status, and lock actions and desynchronize `near/omni-bridge/src/lib.rs::process_fin_transfer_to_near` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `recipient or fee-recipient rebinding` attack class because marks the transfer finalised, optionally redirects payout to the fast-transfer relayer, checks storage-deposit actions for recipient and fee recipients, unlocks tokens, sends tokens, and mints fee tokens in the callback, violating `Near-side finalization must never misroute recipient funds, fee funds, or lock state across storage setup, fast-transfer substitution, and callback resolution`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::process_fin_transfer_to_near`
- Entrypoint: `internal path reached from public `fin_transfer``
- Attacker controls: recipient account, predecessor account, transfer message, storage-deposit actions, fast-transfer status, and lock actions
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: Near-side finalization must never misroute recipient funds, fee funds, or lock state across storage setup, fast-transfer substitution, and callback resolution
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::process_fin_transfer_to_near` and the adjacent storage billing and refund bookkeeping after every branch.
