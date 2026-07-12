# Q396: Keeper.EstimateGas - Gas Cap Lower Than Intrinsic Handled Inconsistently

## Question
Can an unprivileged attacker send public JSON-RPC or gRPC call, estimate, simulate, or trace requests through `public eth_estimateGas via JSON-RPC/gRPC` while controlling `authorizationList` and `baseFee`, under the precondition that the caller supplies state overrides or authorizationList, drive `gRPC EthCall/EstimateGas -> EVMConfig overrides -> read-only StateDB execution` in `x/evm/keeper/grpc_query.go::Keeper.EstimateGas` so that gas cap lower than intrinsic handled inconsistently, violating the invariant that historical block context must not affect live committed funds, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/keeper/grpc_query.go::Keeper.EstimateGas`
- Entrypoint: `public eth_estimateGas via JSON-RPC/gRPC`
- Attacker controls: `authorizationList`, `baseFee`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: gas cap lower than intrinsic handled inconsistently through `gRPC EthCall/EstimateGas -> EVMConfig overrides -> read-only StateDB execution`.
- Invariant to test: historical block context must not affect live committed funds.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: compare IndexBlock/GetTransactionReceipt/GetBlockReceipts output against direct block/result reconstruction for the same transaction.
