# Q3199: Keeper.EthCall - Authorizationlist In Eth Call Mutates Nonce Code Despite Commit False

## Question
Can an unprivileged attacker send public JSON-RPC or gRPC call, estimate, simulate, or trace requests through `public eth_call via JSON-RPC/gRPC` while controlling `from/to/value` and `baseFee`, under the precondition that the RPC request uses a historical or pending block context, drive `gRPC EthCall/EstimateGas -> EVMConfig overrides -> read-only StateDB execution` in `x/evm/keeper/grpc_query.go::Keeper.EthCall` so that authorizationList in eth_call mutates nonce/code despite commit=false, violating the invariant that public simulation must not commit state or hide a committed-path rejection, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/keeper/grpc_query.go::Keeper.EthCall`
- Entrypoint: `public eth_call via JSON-RPC/gRPC`
- Attacker controls: `from/to/value`, `baseFee`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: authorizationList in eth_call mutates nonce/code despite commit=false through `gRPC EthCall/EstimateGas -> EVMConfig overrides -> read-only StateDB execution`.
- Invariant to test: public simulation must not commit state or hide a committed-path rejection.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: construct a contract harness that performs nested CALL/CREATE/SELFDESTRUCT/revert and compare bank keeper balances with StateDB balances.
