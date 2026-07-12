# Q395: Keeper.EthCall - Gascap Zero And Querymaxgaslimit Combine Into Unmetered Execution With A Fund State Side Effect

## Question
Can an unprivileged attacker send public JSON-RPC or gRPC call, estimate, simulate, or trace requests through `public eth_call via JSON-RPC/gRPC` while controlling `state overrides` and `from/to/value`, under the precondition that the simulated call is later submitted as a real transaction, drive `JSON-RPC args -> TransactionArgs.ToMessage -> ApplyMessageWithConfig(commit=false) -> compare committed tx path` in `x/evm/keeper/grpc_query.go::Keeper.EthCall` so that gasCap zero and queryMaxGasLimit combine into unmetered execution with a fund-state side effect, violating the invariant that state overrides must be read-only, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/keeper/grpc_query.go::Keeper.EthCall`
- Entrypoint: `public eth_call via JSON-RPC/gRPC`
- Attacker controls: `state overrides`, `from/to/value`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: gasCap zero and queryMaxGasLimit combine into unmetered execution with a fund-state side effect through `JSON-RPC args -> TransactionArgs.ToMessage -> ApplyMessageWithConfig(commit=false) -> compare committed tx path`.
- Invariant to test: state overrides must be read-only.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: force the edge value at uint64/uint256/sdk.Int boundaries and assert no smaller debit, larger refund, or supply change occurs.
