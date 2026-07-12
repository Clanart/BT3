# Q3943: MsgEthereumTx.VerifySender - Empty From Falls Back Inconsistently Between Rpc And Ante

## Question
Can an unprivileged attacker submit a Cosmos-wrapped Ethereum transaction through `ante verification of signed Ethereum message sender` while controlling `chain ID` and `raw tx payload`, under the precondition that the same Cosmos tx contains more than one MsgEthereumTx, drive `ValidateEthBasic -> VerifyEthSig -> VerifyFee -> EthereumTx -> ApplyTransaction` in `x/evm/types/msg.go::MsgEthereumTx.VerifySender` so that empty From falls back inconsistently between RPC and ante, violating the invariant that Cosmos wrapper metadata must not influence Ethereum signed payload semantics, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/msg.go::MsgEthereumTx.VerifySender`
- Entrypoint: `ante verification of signed Ethereum message sender`
- Attacker controls: `chain ID`, `raw tx payload`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: empty From falls back inconsistently between RPC and ante through `ValidateEthBasic -> VerifyEthSig -> VerifyFee -> EthereumTx -> ApplyTransaction`.
- Invariant to test: Cosmos wrapper metadata must not influence Ethereum signed payload semantics.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: run a local integration test that submits the crafted raw tx through JSON-RPC and compares committed state with direct keeper queries.
