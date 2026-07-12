# Q2502: TransactionArgs.ToSimMessage - Authorizationlist Simulation Reports Code Delegation That Cannot Commit

## Question
Can an unprivileged attacker send public JSON-RPC or gRPC call, estimate, simulate, or trace requests through `eth_simulateV1 conversion to core.Message` while controlling `input/data` and `from/to/value`, under the precondition that the RPC request uses a historical or pending block context, drive `JSON-RPC args -> TransactionArgs.ToMessage -> ApplyMessageWithConfig(commit=false) -> compare committed tx path` in `x/evm/types/tx_args.go::TransactionArgs.ToSimMessage` so that AuthorizationList simulation reports code delegation that cannot commit, violating the invariant that state overrides must be read-only, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/tx_args.go::TransactionArgs.ToSimMessage`
- Entrypoint: `eth_simulateV1 conversion to core.Message`
- Attacker controls: `input/data`, `from/to/value`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: AuthorizationList simulation reports code delegation that cannot commit through `JSON-RPC args -> TransactionArgs.ToMessage -> ApplyMessageWithConfig(commit=false) -> compare committed tx path`.
- Invariant to test: state overrides must be read-only.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: add a fuzz case varying the attacker-controlled fields and differential-check against go-ethereum for tx validity, gas, nonce, and code/storage effects.
