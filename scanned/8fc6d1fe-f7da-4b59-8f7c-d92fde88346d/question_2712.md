# Q2712: Keeper.CalculateBaseFee - Nil Parent Basefee Disables Fee Charging For Dynamic Txs

## Question
Can an unprivileged attacker fill adjacent blocks with carefully priced public transactions through `BeginBlock base-fee calculation from previous block gas` while controlling `consensus MaxGas` and `NoBaseFee`, under the precondition that London rules are active and BaseFee is enabled, drive `EndBlock BlockGasWanted -> BeginBlock CalculateBaseFee -> VerifyFee -> ApplyTransaction` in `x/feemarket/keeper/eip1559.go::Keeper.CalculateBaseFee` so that nil parent baseFee disables fee charging for dynamic txs, violating the invariant that London fee rules must match the active EVM fork rules, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/feemarket/keeper/eip1559.go::Keeper.CalculateBaseFee`
- Entrypoint: `BeginBlock base-fee calculation from previous block gas`
- Attacker controls: `consensus MaxGas`, `NoBaseFee`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: nil parent baseFee disables fee charging for dynamic txs through `EndBlock BlockGasWanted -> BeginBlock CalculateBaseFee -> VerifyFee -> ApplyTransaction`.
- Invariant to test: London fee rules must match the active EVM fork rules.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: compare IndexBlock/GetTransactionReceipt/GetBlockReceipts output against direct block/result reconstruction for the same transaction.
