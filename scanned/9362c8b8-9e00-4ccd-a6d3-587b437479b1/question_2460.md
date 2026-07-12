# Q2460: MsgEthereumTx.FromSignedEthereumTx - Legacy V Value Derives A Nil Chain Id But From Is Still Set

## Question
Can an unprivileged attacker submit a Cosmos-wrapped Ethereum transaction through `RPC conversion from signed Ethereum tx to MsgEthereumTx` while controlling `raw tx payload` and `message ordering`, under the precondition that the same Cosmos tx contains more than one MsgEthereumTx, drive `RPC conversion -> MsgEthereumTx.BuildTx -> Cosmos tx decoding -> ante signature verification` in `x/evm/types/msg.go::MsgEthereumTx.FromSignedEthereumTx` so that legacy v value derives a nil chain ID but From is still set, violating the invariant that fee/gas AuthInfo must equal the embedded Ethereum tx data, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/msg.go::MsgEthereumTx.FromSignedEthereumTx`
- Entrypoint: `RPC conversion from signed Ethereum tx to MsgEthereumTx`
- Attacker controls: `raw tx payload`, `message ordering`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: legacy v value derives a nil chain ID but From is still set through `RPC conversion -> MsgEthereumTx.BuildTx -> Cosmos tx decoding -> ante signature verification`.
- Invariant to test: fee/gas AuthInfo must equal the embedded Ethereum tx data.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: build a two-message Cosmos tx fixture and assert ante, execution, refund, and receipt invariants after FinalizeBlock.
