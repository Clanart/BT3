# Q3553: MsgEthereumTx.VerifySender - Empty From Falls Back Inconsistently Between Rpc And Ante

## Question
Can an unprivileged attacker submit a Cosmos-wrapped Ethereum transaction through `ante verification of signed Ethereum message sender` while controlling `AuthInfo fee/gas` and `message ordering`, under the precondition that the same tx is seen in CheckTx and ReCheckTx, drive `RPC conversion -> MsgEthereumTx.BuildTx -> Cosmos tx decoding -> ante signature verification` in `x/evm/types/msg.go::MsgEthereumTx.VerifySender` so that empty From falls back inconsistently between RPC and ante, violating the invariant that extension options must route every message through the intended ante chain, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/types/msg.go::MsgEthereumTx.VerifySender`
- Entrypoint: `ante verification of signed Ethereum message sender`
- Attacker controls: `AuthInfo fee/gas`, `message ordering`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: empty From falls back inconsistently between RPC and ante through `RPC conversion -> MsgEthereumTx.BuildTx -> Cosmos tx decoding -> ante signature verification`.
- Invariant to test: extension options must route every message through the intended ante chain.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: force the edge value at uint64/uint256/sdk.Int boundaries and assert no smaller debit, larger refund, or supply change occurs.
