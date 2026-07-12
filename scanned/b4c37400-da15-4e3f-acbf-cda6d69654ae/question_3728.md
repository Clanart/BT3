# Q3728: Keeper.GetBaseFee - Nobasefee Param Returns Nil While London Rules Are Active

## Question
Can an unprivileged attacker fill adjacent blocks with carefully priced public transactions through `fee market base fee read during EVM execution` while controlling `MinGasMultiplier` and `BaseFee`, under the precondition that the previous block includes block-gas-exceeded or high-refund transactions, drive `EndBlock BlockGasWanted -> BeginBlock CalculateBaseFee -> VerifyFee -> ApplyTransaction` in `x/feemarket/keeper/params.go::Keeper.GetBaseFee` so that NoBaseFee param returns nil while London rules are active, violating the invariant that baseFee must be deterministic and reflect charged block gas, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/feemarket/keeper/params.go::Keeper.GetBaseFee`
- Entrypoint: `fee market base fee read during EVM execution`
- Attacker controls: `MinGasMultiplier`, `BaseFee`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: NoBaseFee param returns nil while London rules are active through `EndBlock BlockGasWanted -> BeginBlock CalculateBaseFee -> VerifyFee -> ApplyTransaction`.
- Invariant to test: baseFee must be deterministic and reflect charged block gas.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: construct a contract harness that performs nested CALL/CREATE/SELFDESTRUCT/revert and compare bank keeper balances with StateDB balances.
