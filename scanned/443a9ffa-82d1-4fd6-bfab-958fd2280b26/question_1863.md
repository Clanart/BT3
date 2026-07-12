# Q1863: Keeper.Simulate - State Overrides Survive Between Simulated Block Calls

## Question
Can an unprivileged attacker send public JSON-RPC or gRPC call, estimate, simulate, or trace requests through `public eth_simulateV1-style simulation request` while controlling `pending nonce` and `authorizationList`, under the precondition that the account balance or nonce changes between simulation and submission, drive `SetTxDefaults -> EstimateGas/EthCall -> ToTransaction -> signed raw submission` in `x/evm/keeper/simulate.go::Keeper.Simulate` so that state overrides survive between simulated block calls, violating the invariant that historical block context must not affect live committed funds, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/keeper/simulate.go::Keeper.Simulate`
- Entrypoint: `public eth_simulateV1-style simulation request`
- Attacker controls: `pending nonce`, `authorizationList`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: state overrides survive between simulated block calls through `SetTxDefaults -> EstimateGas/EthCall -> ToTransaction -> signed raw submission`.
- Invariant to test: historical block context must not affect live committed funds.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: build a two-message Cosmos tx fixture and assert ante, execution, refund, and receipt invariants after FinalizeBlock.
