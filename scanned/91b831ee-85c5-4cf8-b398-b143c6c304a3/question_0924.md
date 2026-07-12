# Q924: Backend.GetBlockReceipts - Receipt List Omits Block Gas Exceeded Txs That Were Charged

## Question
Can an unprivileged attacker query protocol receipts or logs for crafted included Ethereum transactions through `eth_getBlockReceipts public JSON-RPC` while controlling `tx hash` and `receipt status`, under the precondition that multiple Ethereum messages appear in one Cosmos transaction, drive `block-scoped receipt rebuild -> TxResult lookup -> Cronos-controlled accounting consumer` in `rpc/backend/blocks.go::Backend.GetBlockReceipts` so that receipt list omits block-gas-exceeded txs that were charged, violating the invariant that failed transactions must not be represented as successful fund transfers, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `rpc/backend/blocks.go::Backend.GetBlockReceipts`
- Entrypoint: `eth_getBlockReceipts public JSON-RPC`
- Attacker controls: `tx hash`, `receipt status`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: receipt list omits block-gas-exceeded txs that were charged through `block-scoped receipt rebuild -> TxResult lookup -> Cronos-controlled accounting consumer`.
- Invariant to test: failed transactions must not be represented as successful fund transfers.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: replay the same scenario through eth_call or estimateGas and through eth_sendRawTransaction and assert the only difference is persistence.
