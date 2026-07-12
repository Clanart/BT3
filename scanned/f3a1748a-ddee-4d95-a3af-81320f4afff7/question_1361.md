# Q1361: AuthzLimiterDecorator.AnteHandle - Disabled Message Type Is Hidden Inside Nested Any

## Question
Can an unprivileged attacker submit an authz MsgExec transaction with nested public messages through `public Cosmos authz MsgExec transaction` while controlling `EIP-712 payload` and `type URLs`, under the precondition that the wrapped messages include fund-moving Cosmos operations, drive `EIP-712/Web3Tx authz payload -> signer validation -> fund-moving message` in `ante/cosmos/authz.go::AuthzLimiterDecorator.AnteHandle` so that disabled message type is hidden inside nested Any, violating the invariant that signer identity must survive wrapping and nesting, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `ante/cosmos/authz.go::AuthzLimiterDecorator.AnteHandle`
- Entrypoint: `public Cosmos authz MsgExec transaction`
- Attacker controls: `EIP-712 payload`, `type URLs`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: disabled message type is hidden inside nested Any through `EIP-712/Web3Tx authz payload -> signer validation -> fund-moving message`.
- Invariant to test: signer identity must survive wrapping and nesting.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: compare IndexBlock/GetTransactionReceipt/GetBlockReceipts output against direct block/result reconstruction for the same transaction.
