# Q1104: RejectMessagesDecorator.AnteHandle - Rejected Message Still Has Fees Deducted Before Error

## Question
Can an unprivileged attacker submit an authz MsgExec transaction with nested public messages through `Cosmos ante rejection of direct EVM messages on legacy paths` while controlling `type URLs` and `nested Any messages`, under the precondition that MsgExec contains nested Any messages, drive `RejectMessagesDecorator -> authz nested Any inspection -> Cosmos handler execution` in `ante/cosmos/reject_msgs.go::RejectMessagesDecorator.AnteHandle` so that rejected message still has fees deducted before error, violating the invariant that signer identity must survive wrapping and nesting, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `ante/cosmos/reject_msgs.go::RejectMessagesDecorator.AnteHandle`
- Entrypoint: `Cosmos ante rejection of direct EVM messages on legacy paths`
- Attacker controls: `type URLs`, `nested Any messages`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: rejected message still has fees deducted before error through `RejectMessagesDecorator -> authz nested Any inspection -> Cosmos handler execution`.
- Invariant to test: signer identity must survive wrapping and nesting.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: build a two-message Cosmos tx fixture and assert ante, execution, refund, and receipt invariants after FinalizeBlock.
