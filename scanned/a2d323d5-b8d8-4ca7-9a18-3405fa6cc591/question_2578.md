# Q2578: TransactionArgs.ToTransaction - Authorizationlist Non Nil Selects Setcodetx Without Required Fields

## Question
Can an unprivileged attacker send public JSON-RPC or gRPC call, estimate, simulate, or trace requests through `RPC transaction args converted to Ethereum tx` while controlling `gas cap` and `authorizationList`, under the precondition that the caller supplies state overrides or authorizationList, drive `SetTxDefaults -> EstimateGas/EthCall -> ToTransaction -> signed raw submission` in `x/evm/types/tx_args.go::TransactionArgs.ToTransaction` so that AuthorizationList non-nil selects SetCodeTx without required fields, violating the invariant that historical block context must not affect live committed funds, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/tx_args.go::TransactionArgs.ToTransaction`
- Entrypoint: `RPC transaction args converted to Ethereum tx`
- Attacker controls: `gas cap`, `authorizationList`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: AuthorizationList non-nil selects SetCodeTx without required fields through `SetTxDefaults -> EstimateGas/EthCall -> ToTransaction -> signed raw submission`.
- Invariant to test: historical block context must not affect live committed funds.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: compare IndexBlock/GetTransactionReceipt/GetBlockReceipts output against direct block/result reconstruction for the same transaction.
