# Q1049: AuthList.ToEthAuthList - Duplicate Authorizations Preserve Order Differently Than Geth

## Question
Can an unprivileged attacker submit an EIP-7702 SetCode transaction through `EIP-7702 authorization list conversion` while controlling `delegation address` and `zero-address clear tuple`, under the precondition that the authority account has a nonce and may already have delegation code, drive `ValidateEthBasic -> SetCodeAuthorizations check -> EVM CALL -> durable authorization replay` in `x/evm/types/auth_list.go::AuthList.ToEthAuthList` so that duplicate authorizations preserve order differently than geth, violating the invariant that EIP-7702 may only mutate code/nonce for the recovered authority with a valid nonce and chain ID, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/auth_list.go::AuthList.ToEthAuthList`
- Entrypoint: `EIP-7702 authorization list conversion`
- Attacker controls: `delegation address`, `zero-address clear tuple`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: duplicate authorizations preserve order differently than geth through `ValidateEthBasic -> SetCodeAuthorizations check -> EVM CALL -> durable authorization replay`.
- Invariant to test: EIP-7702 may only mutate code/nonce for the recovered authority with a valid nonce and chain ID.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: construct a contract harness that performs nested CALL/CREATE/SELFDESTRUCT/revert and compare bank keeper balances with StateDB balances.
