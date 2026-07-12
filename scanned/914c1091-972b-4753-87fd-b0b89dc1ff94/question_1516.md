# Q1516: TransactionArgs.ToTransaction - From Field Sets Msgethereumtx From Without Signature Proof

## Question
Can an unprivileged attacker send public JSON-RPC or gRPC call, estimate, simulate, or trace requests through `RPC transaction args converted to Ethereum tx` while controlling `state overrides` and `block number/hash`, under the precondition that the simulated call is later submitted as a real transaction, drive `gRPC EthCall/EstimateGas -> EVMConfig overrides -> read-only StateDB execution` in `x/evm/types/tx_args.go::TransactionArgs.ToTransaction` so that From field sets MsgEthereumTx.From without signature proof, violating the invariant that public simulation must not commit state or hide a committed-path rejection, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/tx_args.go::TransactionArgs.ToTransaction`
- Entrypoint: `RPC transaction args converted to Ethereum tx`
- Attacker controls: `state overrides`, `block number/hash`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: From field sets MsgEthereumTx.From without signature proof through `gRPC EthCall/EstimateGas -> EVMConfig overrides -> read-only StateDB execution`.
- Invariant to test: public simulation must not commit state or hide a committed-path rejection.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: construct a contract harness that performs nested CALL/CREATE/SELFDESTRUCT/revert and compare bank keeper balances with StateDB balances.
