# Q1281: BlockGasLimit - Block Gas Limit Truncates Int64 To Uint64 Incorrectly

## Question
Can an unprivileged attacker submit a valid transaction with adversarial gas and fee fields through `block gas limit lookup for EVM execution and estimates` while controlling `EVM-denom balance` and `gas limit`, under the precondition that the transaction consumes near its gas limit but remains valid, drive `VerifyFee -> DeductTxCostsFromUserBalance -> ApplyMessageWithConfig -> RefundGasWithPrice` in `types/block.go::BlockGasLimit` so that block gas limit truncates int64 to uint64 incorrectly, violating the invariant that a valid tx must never receive a refund greater than escrowed fees, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `types/block.go::BlockGasLimit`
- Entrypoint: `block gas limit lookup for EVM execution and estimates`
- Attacker controls: `EVM-denom balance`, `gas limit`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: block gas limit truncates int64 to uint64 incorrectly through `VerifyFee -> DeductTxCostsFromUserBalance -> ApplyMessageWithConfig -> RefundGasWithPrice`.
- Invariant to test: a valid tx must never receive a refund greater than escrowed fees.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: force the edge value at uint64/uint256/sdk.Int boundaries and assert no smaller debit, larger refund, or supply change occurs.
