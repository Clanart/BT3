# Q1210: VerifyEthSig - Set Code Tx Sender Verified But Authorization Signer Not Tied To Authority

## Question
Can an unprivileged attacker submit a Cosmos-wrapped Ethereum transaction through `CheckTx/FinalizeBlock ante signature verification` while controlling `From bytes` and `message ordering`, under the precondition that the Cosmos wrapper contains exactly one Ethereum extension option, drive `RPC conversion -> MsgEthereumTx.BuildTx -> Cosmos tx decoding -> ante signature verification` in `ante/sigverify.go::VerifyEthSig` so that set-code tx sender verified but authorization signer not tied to authority, violating the invariant that fee/gas AuthInfo must equal the embedded Ethereum tx data, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `ante/sigverify.go::VerifyEthSig`
- Entrypoint: `CheckTx/FinalizeBlock ante signature verification`
- Attacker controls: `From bytes`, `message ordering`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: set-code tx sender verified but authorization signer not tied to authority through `RPC conversion -> MsgEthereumTx.BuildTx -> Cosmos tx decoding -> ante signature verification`.
- Invariant to test: fee/gas AuthInfo must equal the embedded Ethereum tx data.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: compare IndexBlock/GetTransactionReceipt/GetBlockReceipts output against direct block/result reconstruction for the same transaction.
