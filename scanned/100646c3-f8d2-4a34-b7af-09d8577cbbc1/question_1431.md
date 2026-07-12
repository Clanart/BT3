# Q1431: TransactionArgs.ToMessage - Basefee Nil Chooses Legacy Gas Semantics On London Block

## Question
Can an unprivileged attacker send public JSON-RPC or gRPC call, estimate, simulate, or trace requests through `eth_call/estimateGas conversion to core.Message` while controlling `state overrides` and `from/to/value`, under the precondition that the simulated call is later submitted as a real transaction, drive `gRPC EthCall/EstimateGas -> EVMConfig overrides -> read-only StateDB execution` in `x/evm/types/tx_args.go::TransactionArgs.ToMessage` so that baseFee nil chooses legacy gas semantics on London block, violating the invariant that state overrides must be read-only, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/tx_args.go::TransactionArgs.ToMessage`
- Entrypoint: `eth_call/estimateGas conversion to core.Message`
- Attacker controls: `state overrides`, `from/to/value`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: baseFee nil chooses legacy gas semantics on London block through `gRPC EthCall/EstimateGas -> EVMConfig overrides -> read-only StateDB execution`.
- Invariant to test: state overrides must be read-only.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: force the edge value at uint64/uint256/sdk.Int boundaries and assert no smaller debit, larger refund, or supply change occurs.
