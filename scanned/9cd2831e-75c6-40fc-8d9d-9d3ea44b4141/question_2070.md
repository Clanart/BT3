# Q2070: MsgEthereumTx.FromSignedEthereumTx - Legacy V Value Derives A Nil Chain Id But From Is Still Set

## Question
Can an unprivileged attacker submit a Cosmos-wrapped Ethereum transaction through `RPC conversion from signed Ethereum tx to MsgEthereumTx` while controlling `extension options` and `signature values`, under the precondition that the same tx is seen in CheckTx and ReCheckTx, drive `NewAnteHandler route selection -> Ethereum ante decorators -> MsgEthereumTx execution` in `x/evm/types/msg.go::MsgEthereumTx.FromSignedEthereumTx` so that legacy v value derives a nil chain ID but From is still set, violating the invariant that the authenticated signer must be the only account whose nonce, balance, or code can change, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/msg.go::MsgEthereumTx.FromSignedEthereumTx`
- Entrypoint: `RPC conversion from signed Ethereum tx to MsgEthereumTx`
- Attacker controls: `extension options`, `signature values`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: legacy v value derives a nil chain ID but From is still set through `NewAnteHandler route selection -> Ethereum ante decorators -> MsgEthereumTx execution`.
- Invariant to test: the authenticated signer must be the only account whose nonce, balance, or code can change.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: write a focused Go unit test around the target function and assert bank supply, sender balance, nonce, code hash, logs, and receipt status before and after.
