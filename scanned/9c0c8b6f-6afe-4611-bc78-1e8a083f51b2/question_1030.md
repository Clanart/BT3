# Q1030: Keeper.GetNonce - Eip 7702 Authority Nonce Shares Account Sequence With Tx Sender

## Question
Can an unprivileged attacker submit replay, reorder, or replacement transactions from attacker-controlled accounts through `nonce read for RPC, ante, and EVM execution` while controlling `pending nonce` and `multi-message order`, under the precondition that EIP-7702 authority nonce and tx sender nonce are both touched, drive `contract creation nonce reset -> nested CREATE -> final nonce restore` in `x/evm/keeper/keeper.go::Keeper.GetNonce` so that EIP-7702 authority nonce shares account sequence with tx sender, violating the invariant that nonces must increase exactly once for each committed sender or authority action, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/keeper/keeper.go::Keeper.GetNonce`
- Entrypoint: `nonce read for RPC, ante, and EVM execution`
- Attacker controls: `pending nonce`, `multi-message order`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: EIP-7702 authority nonce shares account sequence with tx sender through `contract creation nonce reset -> nested CREATE -> final nonce restore`.
- Invariant to test: nonces must increase exactly once for each committed sender or authority action.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: write a focused Go unit test around the target function and assert bank supply, sender balance, nonce, code hash, logs, and receipt status before and after.
