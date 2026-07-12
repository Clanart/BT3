# Q3238: VerifyEthSig - Set Code Tx Sender Verified But Authorization Signer Not Tied To Authority

## Question
Can an unprivileged attacker submit a Cosmos-wrapped Ethereum transaction through `CheckTx/FinalizeBlock ante signature verification` while controlling `AuthInfo fee/gas` and `message ordering`, under the precondition that the same tx is seen in CheckTx and ReCheckTx, drive `NewAnteHandler route selection -> Ethereum ante decorators -> MsgEthereumTx execution` in `ante/sigverify.go::VerifyEthSig` so that set-code tx sender verified but authorization signer not tied to authority, violating the invariant that extension options must route every message through the intended ante chain, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `ante/sigverify.go::VerifyEthSig`
- Entrypoint: `CheckTx/FinalizeBlock ante signature verification`
- Attacker controls: `AuthInfo fee/gas`, `message ordering`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: set-code tx sender verified but authorization signer not tied to authority through `NewAnteHandler route selection -> Ethereum ante decorators -> MsgEthereumTx execution`.
- Invariant to test: extension options must route every message through the intended ante chain.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: force the edge value at uint64/uint256/sdk.Int boundaries and assert no smaller debit, larger refund, or supply change occurs.
