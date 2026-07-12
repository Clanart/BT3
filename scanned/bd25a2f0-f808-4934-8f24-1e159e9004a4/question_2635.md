# Q2635: Keeper.EndBlock - Block Gas Exceeded Txs Distort Next Basefee

## Question
Can an unprivileged attacker fill adjacent blocks with carefully priced public transactions through `EndBlock update of BlockGasWanted` while controlling `consensus MaxGas` and `BaseFee`, under the precondition that London rules are active and BaseFee is enabled, drive `EndBlock BlockGasWanted -> BeginBlock CalculateBaseFee -> VerifyFee -> ApplyTransaction` in `x/feemarket/keeper/abci.go::Keeper.EndBlock` so that block-gas-exceeded txs distort next baseFee, violating the invariant that BlockGasWanted must not be attacker-lowered without paying gas, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/feemarket/keeper/abci.go::Keeper.EndBlock`
- Entrypoint: `EndBlock update of BlockGasWanted`
- Attacker controls: `consensus MaxGas`, `BaseFee`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: block-gas-exceeded txs distort next baseFee through `EndBlock BlockGasWanted -> BeginBlock CalculateBaseFee -> VerifyFee -> ApplyTransaction`.
- Invariant to test: BlockGasWanted must not be attacker-lowered without paying gas.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: run a local integration test that submits the crafted raw tx through JSON-RPC and compares committed state with direct keeper queries.
