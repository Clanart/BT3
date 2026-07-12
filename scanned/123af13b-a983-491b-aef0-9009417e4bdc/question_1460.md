# Q1460: Keeper.GetNonce - Eip 7702 Authority Nonce Shares Account Sequence With Tx Sender

## Question
Can an unprivileged attacker submit replay, reorder, or replacement transactions from attacker-controlled accounts through `nonce read for RPC, ante, and EVM execution` while controlling `authority nonce` and `pending nonce`, under the precondition that the tx batch contains reordered nonces, drive `GetNonce -> ante nonce check -> SetNonce in EVM -> Commit` in `x/evm/keeper/keeper.go::Keeper.GetNonce` so that EIP-7702 authority nonce shares account sequence with tx sender, violating the invariant that contract creation nonce math must match geth, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/keeper/keeper.go::Keeper.GetNonce`
- Entrypoint: `nonce read for RPC, ante, and EVM execution`
- Attacker controls: `authority nonce`, `pending nonce`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: EIP-7702 authority nonce shares account sequence with tx sender through `GetNonce -> ante nonce check -> SetNonce in EVM -> Commit`.
- Invariant to test: contract creation nonce math must match geth.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: build a two-message Cosmos tx fixture and assert ante, execution, refund, and receipt invariants after FinalizeBlock.
