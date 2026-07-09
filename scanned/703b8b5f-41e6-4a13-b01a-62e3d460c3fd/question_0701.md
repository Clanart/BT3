# Q701: NEAR add_fast_transfer fee payout and storage refund overlap

## Question
Can an unprivileged attacker exploit `internal state writer reached from public fast-finalization flows` so that `near/omni-bridge/src/lib.rs::add_fast_transfer` both refunds reserved storage and pays a fee out of the same economic event because of persists relayer-sponsored fast-transfer state with `finalised = false` and reserves storage for later settlement, violating `one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fast_transfer`
- Entrypoint: `internal state writer reached from public fast-finalization flows`
- Attacker controls: fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id
- Exploit idea: Target callbacks that remove state and refund storage while also minting or transferring fees.
- Invariant to test: one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model every success/failure order and assert that one event cannot produce both the intended fee and an unintended storage rebate for the attacker.
