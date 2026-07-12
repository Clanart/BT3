# Q894: Keeper.GetBaseFee - Nobasefee Param Returns Nil While London Rules Are Active

## Question
Can an unprivileged attacker fill adjacent blocks with carefully priced public transactions through `fee market base fee read during EVM execution` while controlling `block gas used` and `consensus MaxGas`, under the precondition that London rules are active and BaseFee is enabled, drive `BlockGasUsed/BlockGasWanted accounting -> MinGasMultiplier -> next-block baseFee` in `x/feemarket/keeper/params.go::Keeper.GetBaseFee` so that NoBaseFee param returns nil while London rules are active, violating the invariant that London fee rules must match the active EVM fork rules, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/feemarket/keeper/params.go::Keeper.GetBaseFee`
- Entrypoint: `fee market base fee read during EVM execution`
- Attacker controls: `block gas used`, `consensus MaxGas`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: NoBaseFee param returns nil while London rules are active through `BlockGasUsed/BlockGasWanted accounting -> MinGasMultiplier -> next-block baseFee`.
- Invariant to test: London fee rules must match the active EVM fork rules.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: compare IndexBlock/GetTransactionReceipt/GetBlockReceipts output against direct block/result reconstruction for the same transaction.
