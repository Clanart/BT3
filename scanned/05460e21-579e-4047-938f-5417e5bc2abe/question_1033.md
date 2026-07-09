# Q1033: NEAR add_fast_transfer fee payout and storage refund overlap through cross-module drift

## Question
Can an unprivileged attacker use `internal state writer reached from public fast-finalization flows` with control over fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id and desynchronize `near/omni-bridge/src/lib.rs::add_fast_transfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `fee payout and storage refund overlap` attack class because persists relayer-sponsored fast-transfer state with `finalised = false` and reserves storage for later settlement, violating `one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fast_transfer`
- Entrypoint: `internal state writer reached from public fast-finalization flows`
- Attacker controls: fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id
- Exploit idea: Target callbacks that remove state and refund storage while also minting or transferring fees. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model every success/failure order and assert that one event cannot produce both the intended fee and an unintended storage rebate for the attacker. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::add_fast_transfer` and the adjacent replay-protection bookkeeping after every branch.
