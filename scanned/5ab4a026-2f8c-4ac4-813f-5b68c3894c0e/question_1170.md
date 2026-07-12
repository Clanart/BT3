# Q1170: Keeper.EstimateGas - Binary Search Uses Executiongasused And Underestimates Minimum Gas Multiplier Charge

## Question
Can an unprivileged attacker send public JSON-RPC or gRPC call, estimate, simulate, or trace requests through `public eth_estimateGas via JSON-RPC/gRPC` while controlling `from/to/value` and `input/data`, under the precondition that the RPC request uses a historical or pending block context, drive `gRPC EthCall/EstimateGas -> EVMConfig overrides -> read-only StateDB execution` in `x/evm/keeper/grpc_query.go::Keeper.EstimateGas` so that binary search uses ExecutionGasUsed and underestimates minimum gas multiplier charge, violating the invariant that state overrides must be read-only, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/keeper/grpc_query.go::Keeper.EstimateGas`
- Entrypoint: `public eth_estimateGas via JSON-RPC/gRPC`
- Attacker controls: `from/to/value`, `input/data`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: binary search uses ExecutionGasUsed and underestimates minimum gas multiplier charge through `gRPC EthCall/EstimateGas -> EVMConfig overrides -> read-only StateDB execution`.
- Invariant to test: state overrides must be read-only.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: force the edge value at uint64/uint256/sdk.Int boundaries and assert no smaller debit, larger refund, or supply change occurs.
