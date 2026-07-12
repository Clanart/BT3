# Q2888: Keeper.EstimateGas - Gas Cap Lower Than Intrinsic Handled Inconsistently

## Question
Can an unprivileged attacker send public JSON-RPC or gRPC call, estimate, simulate, or trace requests through `public eth_estimateGas via JSON-RPC/gRPC` while controlling `pending nonce` and `from/to/value`, under the precondition that the account balance or nonce changes between simulation and submission, drive `gRPC EthCall/EstimateGas -> EVMConfig overrides -> read-only StateDB execution` in `x/evm/keeper/grpc_query.go::Keeper.EstimateGas` so that gas cap lower than intrinsic handled inconsistently, violating the invariant that RPC defaults must match the transaction that is eventually signed or submitted, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/keeper/grpc_query.go::Keeper.EstimateGas`
- Entrypoint: `public eth_estimateGas via JSON-RPC/gRPC`
- Attacker controls: `pending nonce`, `from/to/value`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: gas cap lower than intrinsic handled inconsistently through `gRPC EthCall/EstimateGas -> EVMConfig overrides -> read-only StateDB execution`.
- Invariant to test: RPC defaults must match the transaction that is eventually signed or submitted.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: run a local integration test that submits the crafted raw tx through JSON-RPC and compares committed state with direct keeper queries.
