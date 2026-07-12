# Q3754: Backend.TraceBlock - Filtered Tx Results Skip Block Gas Exceeded Transactions That Changed Fees

## Question
Can an unprivileged attacker request debug_trace* for crafted included transactions or calls through `debug_traceBlock over public JSON-RPC` while controlling `traceReplay` and `block context`, under the precondition that the traced transaction has predecessors in the same block, drive `TraceTransaction -> indexer lookup -> predecessor reconstruction -> TraceTx` in `rpc/backend/tracing.go::Backend.TraceBlock` so that filtered tx results skip block-gas-exceeded transactions that changed fees, violating the invariant that traceReplay must not mask balance/fee invariants, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `rpc/backend/tracing.go::Backend.TraceBlock`
- Entrypoint: `debug_traceBlock over public JSON-RPC`
- Attacker controls: `traceReplay`, `block context`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: filtered tx results skip block-gas-exceeded transactions that changed fees through `TraceTransaction -> indexer lookup -> predecessor reconstruction -> TraceTx`.
- Invariant to test: traceReplay must not mask balance/fee invariants.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: force the edge value at uint64/uint256/sdk.Int boundaries and assert no smaller debit, larger refund, or supply change occurs.
