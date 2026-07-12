# Q353: MsgEthereumTx.FromSignedEthereumTx - Legacy V Value Derives A Nil Chain Id But From Is Still Set

## Question
Can an unprivileged attacker submit a Cosmos-wrapped Ethereum transaction through `RPC conversion from signed Ethereum tx to MsgEthereumTx` while controlling `AuthInfo fee/gas` and `deprecated fields`, under the precondition that the same tx is seen in CheckTx and ReCheckTx, drive `ValidateEthBasic -> VerifyEthSig -> VerifyFee -> EthereumTx -> ApplyTransaction` in `x/evm/types/msg.go::MsgEthereumTx.FromSignedEthereumTx` so that legacy v value derives a nil chain ID but From is still set, violating the invariant that the authenticated signer must be the only account whose nonce, balance, or code can change, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/msg.go::MsgEthereumTx.FromSignedEthereumTx`
- Entrypoint: `RPC conversion from signed Ethereum tx to MsgEthereumTx`
- Attacker controls: `AuthInfo fee/gas`, `deprecated fields`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: legacy v value derives a nil chain ID but From is still set through `ValidateEthBasic -> VerifyEthSig -> VerifyFee -> EthereumTx -> ApplyTransaction`.
- Invariant to test: the authenticated signer must be the only account whose nonce, balance, or code can change.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: construct a contract harness that performs nested CALL/CREATE/SELFDESTRUCT/revert and compare bank keeper balances with StateDB balances.
