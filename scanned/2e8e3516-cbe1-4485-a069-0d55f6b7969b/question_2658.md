# Q2658: TransactionArgs.ToSimMessage - Zero Gas Simulation Masks Real Min Gas Multiplier Charge

## Question
Can an unprivileged attacker send public JSON-RPC or gRPC call, estimate, simulate, or trace requests through `eth_simulateV1 conversion to core.Message` while controlling `state overrides` and `baseFee`, under the precondition that the simulated call is later submitted as a real transaction, drive `SetTxDefaults -> EstimateGas/EthCall -> ToTransaction -> signed raw submission` in `x/evm/types/tx_args.go::TransactionArgs.ToSimMessage` so that zero gas simulation masks real min-gas multiplier charge, violating the invariant that public simulation must not commit state or hide a committed-path rejection, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/tx_args.go::TransactionArgs.ToSimMessage`
- Entrypoint: `eth_simulateV1 conversion to core.Message`
- Attacker controls: `state overrides`, `baseFee`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: zero gas simulation masks real min-gas multiplier charge through `SetTxDefaults -> EstimateGas/EthCall -> ToTransaction -> signed raw submission`.
- Invariant to test: public simulation must not commit state or hide a committed-path rejection.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: write a focused Go unit test around the target function and assert bank supply, sender balance, nonce, code hash, logs, and receipt status before and after.
