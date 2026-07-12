# Q3515: TransactionArgs.ToMessage - Authorizationlist Is Accepted In Read Only Calls And Changes Gas State Behavior

## Question
Can an unprivileged attacker send public JSON-RPC or gRPC call, estimate, simulate, or trace requests through `eth_call/estimateGas conversion to core.Message` while controlling `from/to/value` and `baseFee`, under the precondition that the RPC request uses a historical or pending block context, drive `JSON-RPC args -> TransactionArgs.ToMessage -> ApplyMessageWithConfig(commit=false) -> compare committed tx path` in `x/evm/types/tx_args.go::TransactionArgs.ToMessage` so that AuthorizationList is accepted in read-only calls and changes gas/state behavior, violating the invariant that public simulation must not commit state or hide a committed-path rejection, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/tx_args.go::TransactionArgs.ToMessage`
- Entrypoint: `eth_call/estimateGas conversion to core.Message`
- Attacker controls: `from/to/value`, `baseFee`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: AuthorizationList is accepted in read-only calls and changes gas/state behavior through `JSON-RPC args -> TransactionArgs.ToMessage -> ApplyMessageWithConfig(commit=false) -> compare committed tx path`.
- Invariant to test: public simulation must not commit state or hide a committed-path rejection.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: construct a contract harness that performs nested CALL/CREATE/SELFDESTRUCT/revert and compare bank keeper balances with StateDB balances.
