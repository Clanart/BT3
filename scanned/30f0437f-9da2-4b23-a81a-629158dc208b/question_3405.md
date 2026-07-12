# Q3405: Keeper.applyDurableAuthorization - Durable Replay Skips Validation That Main State Depended On

## Question
Can an unprivileged attacker submit an EIP-7702 SetCode transaction through `durable replay of validated EIP-7702 authorization effects` while controlling `delegation address` and `authorization ChainID`, under the precondition that the authority account has a nonce and may already have delegation code, drive `SetCodeTx.Validate -> AuthList.ToEthAuthList -> validateAuthorization -> applyAuthorization` in `x/evm/keeper/set_code_authorizations.go::Keeper.applyDurableAuthorization` so that durable replay skips validation that main state depended on, violating the invariant that duplicate authorizations must produce the same result as geth, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/keeper/set_code_authorizations.go::Keeper.applyDurableAuthorization`
- Entrypoint: `durable replay of validated EIP-7702 authorization effects`
- Attacker controls: `delegation address`, `authorization ChainID`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: durable replay skips validation that main state depended on through `SetCodeTx.Validate -> AuthList.ToEthAuthList -> validateAuthorization -> applyAuthorization`.
- Invariant to test: duplicate authorizations must produce the same result as geth.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: add a fuzz case varying the attacker-controlled fields and differential-check against go-ethereum for tx validity, gas, nonce, and code/storage effects.
