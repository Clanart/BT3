# Q744: TransactionArgs.ToSimMessage - Validation Mode Skips Transaction Only Checks Needed For Fund Safety

## Question
Can an unprivileged attacker send public JSON-RPC or gRPC call, estimate, simulate, or trace requests through `eth_simulateV1 conversion to core.Message` while controlling `authorizationList` and `baseFee`, under the precondition that the caller supplies state overrides or authorizationList, drive `JSON-RPC args -> TransactionArgs.ToMessage -> ApplyMessageWithConfig(commit=false) -> compare committed tx path` in `x/evm/types/tx_args.go::TransactionArgs.ToSimMessage` so that validation mode skips transaction-only checks needed for fund safety, violating the invariant that historical block context must not affect live committed funds, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/tx_args.go::TransactionArgs.ToSimMessage`
- Entrypoint: `eth_simulateV1 conversion to core.Message`
- Attacker controls: `authorizationList`, `baseFee`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: validation mode skips transaction-only checks needed for fund safety through `JSON-RPC args -> TransactionArgs.ToMessage -> ApplyMessageWithConfig(commit=false) -> compare committed tx path`.
- Invariant to test: historical block context must not affect live committed funds.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: compare IndexBlock/GetTransactionReceipt/GetBlockReceipts output against direct block/result reconstruction for the same transaction.
