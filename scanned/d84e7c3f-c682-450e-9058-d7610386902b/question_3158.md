# Q3158: NEAR remove_transfer_message flows callback refund creates value gap at boundary values

## Question
Can an unprivileged attacker trigger `public sign/finalize/claim callbacks through transfer cleanup` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::remove_transfer_message/remove_fin_transfer/remove_transfer_message_without_refund` violate `cleanup must never reopen replay protection or double-refund storage while the economic effect of the transfer already escaped` in the `callback refund creates value gap` attack class because cleans up pending-transfer state after signing, fee claim, callback refunds, or finalization failure becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::remove_transfer_message/remove_fin_transfer/remove_transfer_message_without_refund`
- Entrypoint: `public sign/finalize/claim callbacks through transfer cleanup`
- Attacker controls: transfer id, callback success/failure, storage owner, and removal order relative to payout or refund
- Exploit idea: Target `ft_transfer_call`-style paths where refund semantics affect whether state is removed or custody is burned. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: cleanup must never reopen replay protection or double-refund storage while the economic effect of the transfer already escaped
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate every callback result and assert that no branch leaves both user-accessible funds and a still-live bridge claim for the same transfer. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
