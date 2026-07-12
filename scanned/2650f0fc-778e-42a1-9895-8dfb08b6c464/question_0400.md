# Q400: TransactionArgs.ToSimMessage - Authorizationlist Simulation Reports Code Delegation That Cannot Commit

## Question
Can an unprivileged attacker send public JSON-RPC or gRPC call, estimate, simulate, or trace requests through `eth_simulateV1 conversion to core.Message` while controlling `gas cap` and `state overrides`, under the precondition that the caller supplies state overrides or authorizationList, drive `SetTxDefaults -> EstimateGas/EthCall -> ToTransaction -> signed raw submission` in `x/evm/types/tx_args.go::TransactionArgs.ToSimMessage` so that AuthorizationList simulation reports code delegation that cannot commit, violating the invariant that historical block context must not affect live committed funds, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/tx_args.go::TransactionArgs.ToSimMessage`
- Entrypoint: `eth_simulateV1 conversion to core.Message`
- Attacker controls: `gas cap`, `state overrides`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: AuthorizationList simulation reports code delegation that cannot commit through `SetTxDefaults -> EstimateGas/EthCall -> ToTransaction -> signed raw submission`.
- Invariant to test: historical block context must not affect live committed funds.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: build a two-message Cosmos tx fixture and assert ante, execution, refund, and receipt invariants after FinalizeBlock.
