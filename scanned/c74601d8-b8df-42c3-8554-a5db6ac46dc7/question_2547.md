# Q2547: NEAR promise bookkeeping callback refund creates value gap at boundary values

## Question
Can an unprivileged attacker trigger `public yield-resume flow through deferred outbound transfers` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::add_promise/remove_promise/init_transfer_promises` violate `promise bookkeeping must never let an attacker orphan, overwrite, or resume someone else’s pending outbound transfer` in the `callback refund creates value gap` attack class because tracks deferred init-transfer promises by account id so they can resume once storage arrives becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_promise/remove_promise/init_transfer_promises`
- Entrypoint: `public yield-resume flow through deferred outbound transfers`
- Attacker controls: message-storage account id, yielded promise id, repeated funding, and callback timing
- Exploit idea: Target `ft_transfer_call`-style paths where refund semantics affect whether state is removed or custody is burned. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: promise bookkeeping must never let an attacker orphan, overwrite, or resume someone else’s pending outbound transfer
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate every callback result and assert that no branch leaves both user-accessible funds and a still-live bridge claim for the same transfer. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
