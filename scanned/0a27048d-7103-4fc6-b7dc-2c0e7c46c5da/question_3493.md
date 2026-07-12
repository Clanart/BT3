# Q3493: Keeper.EndBlock - Gaswanted Lower Bound Lets Proposer Underpay Future Basefee

## Question
Can an unprivileged attacker fill adjacent blocks with carefully priced public transactions through `EndBlock update of BlockGasWanted` while controlling `NoBaseFee` and `block gas wanted`, under the precondition that the previous block includes block-gas-exceeded or high-refund transactions, drive `CalculateBaseFee -> SetBaseFee -> RPC BaseFee -> dynamic-fee tx admission` in `x/feemarket/keeper/abci.go::Keeper.EndBlock` so that gasWanted lower bound lets proposer underpay future baseFee, violating the invariant that MinGasPrice must remain a lower bound for accepted txs, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/feemarket/keeper/abci.go::Keeper.EndBlock`
- Entrypoint: `EndBlock update of BlockGasWanted`
- Attacker controls: `NoBaseFee`, `block gas wanted`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: gasWanted lower bound lets proposer underpay future baseFee through `CalculateBaseFee -> SetBaseFee -> RPC BaseFee -> dynamic-fee tx admission`.
- Invariant to test: MinGasPrice must remain a lower bound for accepted txs.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: force the edge value at uint64/uint256/sdk.Int boundaries and assert no smaller debit, larger refund, or supply change occurs.
