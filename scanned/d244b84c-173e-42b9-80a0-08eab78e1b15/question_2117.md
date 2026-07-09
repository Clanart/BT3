# Q2117: NEAR public transfer-message reads same fee collectible twice

## Question
Can an unprivileged attacker reach `public off-chain coordination reads for pending transfers` and make `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage` remove or preserve fee-bearing state in a way that allows the same fee to be claimed twice, violating `publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage`
- Entrypoint: `public off-chain coordination reads for pending transfers`
- Attacker controls: transfer id choice and timing relative to sign/claim/finalize callbacks
- Exploit idea: Target pending-transfer cleanup, fast-transfer removal, and replay protection around fee-claim proofs.
- Invariant to test: publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer
- Expected Immunefi impact: Balance manipulation
- Fast validation: Claim once, then replay or mutate non-economic proof fields and assert that no second claim succeeds or blocks unrelated transfers.
