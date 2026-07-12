# Q981: Keeper.EVMBlockConfig - Basefee Defaults To Zero When London Active But Feemarket Param Nil

## Question
Can an unprivileged attacker call a malicious contract from an unprivileged EOA through `per-block EVM config construction for transaction execution` while controlling `EIP-7702 authorization list` and `value`, under the precondition that London and Prague rules are active on the target height, drive `ApplyTransaction -> ApplyMessageWithConfig -> PostTxProcessing -> RefundGas -> ResetGasMeterAndConsumeGas` in `x/evm/keeper/config.go::Keeper.EVMBlockConfig` so that baseFee defaults to zero when London active but feemarket param nil, violating the invariant that post-hook state must be atomic with the EVM transaction, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/keeper/config.go::Keeper.EVMBlockConfig`
- Entrypoint: `per-block EVM config construction for transaction execution`
- Attacker controls: `EIP-7702 authorization list`, `value`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: baseFee defaults to zero when London active but feemarket param nil through `ApplyTransaction -> ApplyMessageWithConfig -> PostTxProcessing -> RefundGas -> ResetGasMeterAndConsumeGas`.
- Invariant to test: post-hook state must be atomic with the EVM transaction.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: compare IndexBlock/GetTransactionReceipt/GetBlockReceipts output against direct block/result reconstruction for the same transaction.
