# Q997: Keeper.EthCall - Proposer Address Override Changes Coinbase Visible Logic Used In Later Tx

## Question
Can an unprivileged attacker send public JSON-RPC or gRPC call, estimate, simulate, or trace requests through `public eth_call via JSON-RPC/gRPC` while controlling `pending nonce` and `gas cap`, under the precondition that the account balance or nonce changes between simulation and submission, drive `gRPC EthCall/EstimateGas -> EVMConfig overrides -> read-only StateDB execution` in `x/evm/keeper/grpc_query.go::Keeper.EthCall` so that proposer address override changes coinbase-visible logic used in later tx, violating the invariant that historical block context must not affect live committed funds, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/keeper/grpc_query.go::Keeper.EthCall`
- Entrypoint: `public eth_call via JSON-RPC/gRPC`
- Attacker controls: `pending nonce`, `gas cap`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: proposer address override changes coinbase-visible logic used in later tx through `gRPC EthCall/EstimateGas -> EVMConfig overrides -> read-only StateDB execution`.
- Invariant to test: historical block context must not affect live committed funds.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: compare IndexBlock/GetTransactionReceipt/GetBlockReceipts output against direct block/result reconstruction for the same transaction.
