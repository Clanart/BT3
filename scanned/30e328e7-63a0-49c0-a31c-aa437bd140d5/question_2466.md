# Q2466: AuthList.ToEthAuthList - Invalid Address String Normalizes To Zero Address

## Question
Can an unprivileged attacker submit an EIP-7702 SetCode transaction through `EIP-7702 authorization list conversion` while controlling `AuthList nil-vs-empty` and `duplicate auth tuples`, under the precondition that duplicate authorization tuples for one authority appear in the same transaction, drive `SetCodeTx.Validate -> AuthList.ToEthAuthList -> validateAuthorization -> applyAuthorization` in `x/evm/types/auth_list.go::AuthList.ToEthAuthList` so that invalid address string normalizes to zero address, violating the invariant that delegation clearing must not target an unintended account, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/auth_list.go::AuthList.ToEthAuthList`
- Entrypoint: `EIP-7702 authorization list conversion`
- Attacker controls: `AuthList nil-vs-empty`, `duplicate auth tuples`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: invalid address string normalizes to zero address through `SetCodeTx.Validate -> AuthList.ToEthAuthList -> validateAuthorization -> applyAuthorization`.
- Invariant to test: delegation clearing must not target an unintended account.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: build a two-message Cosmos tx fixture and assert ante, execution, refund, and receipt invariants after FinalizeBlock.
