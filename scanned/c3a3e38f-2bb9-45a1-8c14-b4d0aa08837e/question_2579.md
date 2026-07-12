# Q2579: TransactionArgs.ToMessage - Skiptransactionchecks Lets Impossible Tx Simulate As Profitable

## Question
Can an unprivileged attacker send public JSON-RPC or gRPC call, estimate, simulate, or trace requests through `eth_call/estimateGas conversion to core.Message` while controlling `input/data` and `state overrides`, under the precondition that the RPC request uses a historical or pending block context, drive `JSON-RPC args -> TransactionArgs.ToMessage -> ApplyMessageWithConfig(commit=false) -> compare committed tx path` in `x/evm/types/tx_args.go::TransactionArgs.ToMessage` so that SkipTransactionChecks lets impossible tx simulate as profitable, violating the invariant that public simulation must not commit state or hide a committed-path rejection, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/tx_args.go::TransactionArgs.ToMessage`
- Entrypoint: `eth_call/estimateGas conversion to core.Message`
- Attacker controls: `input/data`, `state overrides`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: SkipTransactionChecks lets impossible tx simulate as profitable through `JSON-RPC args -> TransactionArgs.ToMessage -> ApplyMessageWithConfig(commit=false) -> compare committed tx path`.
- Invariant to test: public simulation must not commit state or hide a committed-path rejection.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: write a focused Go unit test around the target function and assert bank supply, sender balance, nonce, code hash, logs, and receipt status before and after.
