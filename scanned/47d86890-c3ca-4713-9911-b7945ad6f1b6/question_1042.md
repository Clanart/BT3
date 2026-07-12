# Q1042: MsgEthereumTx.VerifySender - Set Code Tx Recovers Tx Sender While Delegated Authority Is Unchecked

## Question
Can an unprivileged attacker submit a Cosmos-wrapped Ethereum transaction through `ante verification of signed Ethereum message sender` while controlling `raw tx payload` and `chain ID`, under the precondition that the same Cosmos tx contains more than one MsgEthereumTx, drive `ValidateEthBasic -> VerifyEthSig -> VerifyFee -> EthereumTx -> ApplyTransaction` in `x/evm/types/msg.go::MsgEthereumTx.VerifySender` so that set-code tx recovers tx sender while delegated authority is unchecked, violating the invariant that Cosmos wrapper metadata must not influence Ethereum signed payload semantics, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/msg.go::MsgEthereumTx.VerifySender`
- Entrypoint: `ante verification of signed Ethereum message sender`
- Attacker controls: `raw tx payload`, `chain ID`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: set-code tx recovers tx sender while delegated authority is unchecked through `ValidateEthBasic -> VerifyEthSig -> VerifyFee -> EthereumTx -> ApplyTransaction`.
- Invariant to test: Cosmos wrapper metadata must not influence Ethereum signed payload semantics.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: replay the same scenario through eth_call or estimateGas and through eth_sendRawTransaction and assert the only difference is persistence.
