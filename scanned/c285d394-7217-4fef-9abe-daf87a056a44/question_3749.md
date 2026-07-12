# Q3749: TransactionArgs.ToMessage - Skiptransactionchecks Lets Impossible Tx Simulate As Profitable

## Question
Can an unprivileged attacker send public JSON-RPC or gRPC call, estimate, simulate, or trace requests through `eth_call/estimateGas conversion to core.Message` while controlling `gas cap` and `block number/hash`, under the precondition that the caller supplies state overrides or authorizationList, drive `JSON-RPC args -> TransactionArgs.ToMessage -> ApplyMessageWithConfig(commit=false) -> compare committed tx path` in `x/evm/types/tx_args.go::TransactionArgs.ToMessage` so that SkipTransactionChecks lets impossible tx simulate as profitable, violating the invariant that RPC defaults must match the transaction that is eventually signed or submitted, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/tx_args.go::TransactionArgs.ToMessage`
- Entrypoint: `eth_call/estimateGas conversion to core.Message`
- Attacker controls: `gas cap`, `block number/hash`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: SkipTransactionChecks lets impossible tx simulate as profitable through `JSON-RPC args -> TransactionArgs.ToMessage -> ApplyMessageWithConfig(commit=false) -> compare committed tx path`.
- Invariant to test: RPC defaults must match the transaction that is eventually signed or submitted.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: run a local integration test that submits the crafted raw tx through JSON-RPC and compares committed state with direct keeper queries.
