# Q314: TransactionArgs.ToSimMessage - Validation Mode Skips Transaction Only Checks Needed For Fund Safety

## Question
Can an unprivileged attacker send public JSON-RPC or gRPC call, estimate, simulate, or trace requests through `eth_simulateV1 conversion to core.Message` while controlling `baseFee` and `state overrides`, under the precondition that the simulated call is later submitted as a real transaction, drive `gRPC EthCall/EstimateGas -> EVMConfig overrides -> read-only StateDB execution` in `x/evm/types/tx_args.go::TransactionArgs.ToSimMessage` so that validation mode skips transaction-only checks needed for fund safety, violating the invariant that public simulation must not commit state or hide a committed-path rejection, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/tx_args.go::TransactionArgs.ToSimMessage`
- Entrypoint: `eth_simulateV1 conversion to core.Message`
- Attacker controls: `baseFee`, `state overrides`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: validation mode skips transaction-only checks needed for fund safety through `gRPC EthCall/EstimateGas -> EVMConfig overrides -> read-only StateDB execution`.
- Invariant to test: public simulation must not commit state or hide a committed-path rejection.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: construct a contract harness that performs nested CALL/CREATE/SELFDESTRUCT/revert and compare bank keeper balances with StateDB balances.
