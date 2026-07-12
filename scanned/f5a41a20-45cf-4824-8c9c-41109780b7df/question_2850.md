# Q2850: MsgEthereumTx.FromSignedEthereumTx - Legacy V Value Derives A Nil Chain Id But From Is Still Set

## Question
Can an unprivileged attacker submit a Cosmos-wrapped Ethereum transaction through `RPC conversion from signed Ethereum tx to MsgEthereumTx` while controlling `deprecated fields` and `extension options`, under the precondition that the sender has just enough EVM-denom balance for fee plus value, drive `ValidateEthBasic -> VerifyEthSig -> VerifyFee -> EthereumTx -> ApplyTransaction` in `x/evm/types/msg.go::MsgEthereumTx.FromSignedEthereumTx` so that legacy v value derives a nil chain ID but From is still set, violating the invariant that extension options must route every message through the intended ante chain, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/msg.go::MsgEthereumTx.FromSignedEthereumTx`
- Entrypoint: `RPC conversion from signed Ethereum tx to MsgEthereumTx`
- Attacker controls: `deprecated fields`, `extension options`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: legacy v value derives a nil chain ID but From is still set through `ValidateEthBasic -> VerifyEthSig -> VerifyFee -> EthereumTx -> ApplyTransaction`.
- Invariant to test: extension options must route every message through the intended ante chain.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: force the edge value at uint64/uint256/sdk.Int boundaries and assert no smaller debit, larger refund, or supply change occurs.
