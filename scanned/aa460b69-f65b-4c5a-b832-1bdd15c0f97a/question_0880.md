# Q880: Keeper.applyDurableAuthorization - Durable Context Commits Code After Hook Cache Rollback

## Question
Can an unprivileged attacker submit an EIP-7702 SetCode transaction through `durable replay of validated EIP-7702 authorization effects` while controlling `AuthList nil-vs-empty` and `authorization ChainID`, under the precondition that duplicate authorization tuples for one authority appear in the same transaction, drive `ApplyMessageWithConfig -> applyAuthorization -> applyDurableAuthorization -> StateDB.Commit` in `x/evm/keeper/set_code_authorizations.go::Keeper.applyDurableAuthorization` so that durable context commits code after hook cache rollback, violating the invariant that failed or skipped authorizations must not leave durable code or nonce changes, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/keeper/set_code_authorizations.go::Keeper.applyDurableAuthorization`
- Entrypoint: `durable replay of validated EIP-7702 authorization effects`
- Attacker controls: `AuthList nil-vs-empty`, `authorization ChainID`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: durable context commits code after hook cache rollback through `ApplyMessageWithConfig -> applyAuthorization -> applyDurableAuthorization -> StateDB.Commit`.
- Invariant to test: failed or skipped authorizations must not leave durable code or nonce changes.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: run a local integration test that submits the crafted raw tx through JSON-RPC and compares committed state with direct keeper queries.
