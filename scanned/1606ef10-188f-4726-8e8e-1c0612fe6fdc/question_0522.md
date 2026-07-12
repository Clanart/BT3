# Q522: VerifyEthSig - Multi Message Tx Leaves One Ethereum Msg With Stale From

## Question
Can an unprivileged attacker submit a Cosmos-wrapped Ethereum transaction through `CheckTx/FinalizeBlock ante signature verification` while controlling `From bytes` and `message ordering`, under the precondition that the Cosmos wrapper contains exactly one Ethereum extension option, drive `NewAnteHandler route selection -> Ethereum ante decorators -> MsgEthereumTx execution` in `ante/sigverify.go::VerifyEthSig` so that multi-message tx leaves one Ethereum msg with stale From, violating the invariant that fee/gas AuthInfo must equal the embedded Ethereum tx data, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `ante/sigverify.go::VerifyEthSig`
- Entrypoint: `CheckTx/FinalizeBlock ante signature verification`
- Attacker controls: `From bytes`, `message ordering`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: multi-message tx leaves one Ethereum msg with stale From through `NewAnteHandler route selection -> Ethereum ante decorators -> MsgEthereumTx execution`.
- Invariant to test: fee/gas AuthInfo must equal the embedded Ethereum tx data.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: compare IndexBlock/GetTransactionReceipt/GetBlockReceipts output against direct block/result reconstruction for the same transaction.
