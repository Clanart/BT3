# Q1199: NEAR add_fast_transfer fee payout and storage refund overlap at boundary values

## Question
Can an unprivileged attacker trigger `internal state writer reached from public fast-finalization flows` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::add_fast_transfer` violate `one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs` in the `fee payout and storage refund overlap` attack class because persists relayer-sponsored fast-transfer state with `finalised = false` and reserves storage for later settlement becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fast_transfer`
- Entrypoint: `internal state writer reached from public fast-finalization flows`
- Attacker controls: fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id
- Exploit idea: Target callbacks that remove state and refund storage while also minting or transferring fees. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model every success/failure order and assert that one event cannot produce both the intended fee and an unintended storage rebate for the attacker. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
