# Q3103: Keeper.EndBlock - Gaswanted Lower Bound Lets Proposer Underpay Future Basefee

## Question
Can an unprivileged attacker fill adjacent blocks with carefully priced public transactions through `EndBlock update of BlockGasWanted` while controlling `MinGasPrice` and `consensus MaxGas`, under the precondition that consensus MaxGas is near an arithmetic boundary, drive `EndBlock BlockGasWanted -> BeginBlock CalculateBaseFee -> VerifyFee -> ApplyTransaction` in `x/feemarket/keeper/abci.go::Keeper.EndBlock` so that gasWanted lower bound lets proposer underpay future baseFee, violating the invariant that London fee rules must match the active EVM fork rules, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/feemarket/keeper/abci.go::Keeper.EndBlock`
- Entrypoint: `EndBlock update of BlockGasWanted`
- Attacker controls: `MinGasPrice`, `consensus MaxGas`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: gasWanted lower bound lets proposer underpay future baseFee through `EndBlock BlockGasWanted -> BeginBlock CalculateBaseFee -> VerifyFee -> ApplyTransaction`.
- Invariant to test: London fee rules must match the active EVM fork rules.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: build a two-message Cosmos tx fixture and assert ante, execution, refund, and receipt invariants after FinalizeBlock.
