# Q2517: AuthzLimiterDecorator.AnteHandle - Type Url Alias Bypasses Disabled List

## Question
Can an unprivileged attacker submit an authz MsgExec transaction with nested public messages through `public Cosmos authz MsgExec transaction` while controlling `type URLs` and `fee grants`, under the precondition that MsgExec contains nested Any messages, drive `MsgExec decoding -> AuthzLimiterDecorator -> fee deduction -> nested message execution` in `ante/cosmos/authz.go::AuthzLimiterDecorator.AnteHandle` so that type URL alias bypasses disabled list, violating the invariant that nested Any type URLs must be canonical, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `ante/cosmos/authz.go::AuthzLimiterDecorator.AnteHandle`
- Entrypoint: `public Cosmos authz MsgExec transaction`
- Attacker controls: `type URLs`, `fee grants`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: type URL alias bypasses disabled list through `MsgExec decoding -> AuthzLimiterDecorator -> fee deduction -> nested message execution`.
- Invariant to test: nested Any type URLs must be canonical.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: run a local integration test that submits the crafted raw tx through JSON-RPC and compares committed state with direct keeper queries.
