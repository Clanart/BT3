# Q2110: TransactionArgs.ToTransaction - Accesslist Pointer Nil Versus Empty Changes Tx Type

## Question
Can an unprivileged attacker send public JSON-RPC or gRPC call, estimate, simulate, or trace requests through `RPC transaction args converted to Ethereum tx` while controlling `pending nonce` and `block number/hash`, under the precondition that the account balance or nonce changes between simulation and submission, drive `SetTxDefaults -> EstimateGas/EthCall -> ToTransaction -> signed raw submission` in `x/evm/types/tx_args.go::TransactionArgs.ToTransaction` so that AccessList pointer nil versus empty changes tx type, violating the invariant that RPC defaults must match the transaction that is eventually signed or submitted, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/tx_args.go::TransactionArgs.ToTransaction`
- Entrypoint: `RPC transaction args converted to Ethereum tx`
- Attacker controls: `pending nonce`, `block number/hash`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: AccessList pointer nil versus empty changes tx type through `SetTxDefaults -> EstimateGas/EthCall -> ToTransaction -> signed raw submission`.
- Invariant to test: RPC defaults must match the transaction that is eventually signed or submitted.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: replay the same scenario through eth_call or estimateGas and through eth_sendRawTransaction and assert the only difference is persistence.
