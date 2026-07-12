# Q1651: AuthList.ToEthAuthList - Invalid Address String Normalizes To Zero Address

## Question
Can an unprivileged attacker submit an EIP-7702 SetCode transaction through `EIP-7702 authorization list conversion` while controlling `authorization nonce` and `AuthList nil-vs-empty`, under the precondition that the authorization tuple targets an attacker-controlled or victim-approved authority, drive `ApplyMessageWithConfig -> applyAuthorization -> applyDurableAuthorization -> StateDB.Commit` in `x/evm/types/auth_list.go::AuthList.ToEthAuthList` so that invalid address string normalizes to zero address, violating the invariant that failed or skipped authorizations must not leave durable code or nonce changes, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/auth_list.go::AuthList.ToEthAuthList`
- Entrypoint: `EIP-7702 authorization list conversion`
- Attacker controls: `authorization nonce`, `AuthList nil-vs-empty`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: invalid address string normalizes to zero address through `ApplyMessageWithConfig -> applyAuthorization -> applyDurableAuthorization -> StateDB.Commit`.
- Invariant to test: failed or skipped authorizations must not leave durable code or nonce changes.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: replay the same scenario through eth_call or estimateGas and through eth_sendRawTransaction and assert the only difference is persistence.
