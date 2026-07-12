# Q1654: Keeper.applyDurableAuthorization - Nonce Bump Persists While Evm Call Reverts

## Question
Can an unprivileged attacker submit an EIP-7702 SetCode transaction through `durable replay of validated EIP-7702 authorization effects` while controlling `authorization V/R/S` and `zero-address clear tuple`, under the precondition that the EIP-7702 transaction later reverts or hits a post-hook failure, drive `ApplyMessageWithConfig -> applyAuthorization -> applyDurableAuthorization -> StateDB.Commit` in `x/evm/keeper/set_code_authorizations.go::Keeper.applyDurableAuthorization` so that nonce bump persists while EVM call reverts, violating the invariant that EIP-7702 may only mutate code/nonce for the recovered authority with a valid nonce and chain ID, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/keeper/set_code_authorizations.go::Keeper.applyDurableAuthorization`
- Entrypoint: `durable replay of validated EIP-7702 authorization effects`
- Attacker controls: `authorization V/R/S`, `zero-address clear tuple`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: nonce bump persists while EVM call reverts through `ApplyMessageWithConfig -> applyAuthorization -> applyDurableAuthorization -> StateDB.Commit`.
- Invariant to test: EIP-7702 may only mutate code/nonce for the recovered authority with a valid nonce and chain ID.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: write a focused Go unit test around the target function and assert bank supply, sender balance, nonce, code hash, logs, and receipt status before and after.
