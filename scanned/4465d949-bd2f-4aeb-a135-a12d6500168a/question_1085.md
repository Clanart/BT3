# Q1085: Backend.SetTxDefaults - Legacy Gasprice Suggestion Charges Basefee Twice Or Zero

## Question
Can an unprivileged attacker send public JSON-RPC or gRPC call, estimate, simulate, or trace requests through `JSON-RPC transaction argument defaulting` while controlling `from/to/value` and `state overrides`, under the precondition that the RPC request uses a historical or pending block context, drive `gRPC EthCall/EstimateGas -> EVMConfig overrides -> read-only StateDB execution` in `rpc/backend/call_tx.go::Backend.SetTxDefaults` so that legacy gasPrice suggestion charges baseFee twice or zero, violating the invariant that public simulation must not commit state or hide a committed-path rejection, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `rpc/backend/call_tx.go::Backend.SetTxDefaults`
- Entrypoint: `JSON-RPC transaction argument defaulting`
- Attacker controls: `from/to/value`, `state overrides`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: legacy gasPrice suggestion charges baseFee twice or zero through `gRPC EthCall/EstimateGas -> EVMConfig overrides -> read-only StateDB execution`.
- Invariant to test: public simulation must not commit state or hide a committed-path rejection.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: write a focused Go unit test around the target function and assert bank supply, sender balance, nonce, code hash, logs, and receipt status before and after.
