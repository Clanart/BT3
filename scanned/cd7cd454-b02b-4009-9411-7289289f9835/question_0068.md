# Q68: LegacyGetEIP712TypedDataForMsg - Legacy Amino Sign Doc Encodes Signer Differently From Proto Signer

## Question
Can an unprivileged attacker submit a legacy EIP-712/Web3Tx signed Cosmos transaction through `legacy Web3Tx extension-option signing path` while controlling `fee payer` and `message signer`, under the precondition that the payload contains authz or fee delegation, drive `Web3Tx extension route -> authz limiter -> EIP-712 signature verification` in `ethereum/eip712/encoding_legacy.go::LegacyGetEIP712TypedDataForMsg` so that legacy amino sign doc encodes signer differently from proto signer, violating the invariant that legacy typed data must not replay across Cronos chains, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `ethereum/eip712/encoding_legacy.go::LegacyGetEIP712TypedDataForMsg`
- Entrypoint: `legacy Web3Tx extension-option signing path`
- Attacker controls: `fee payer`, `message signer`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: legacy amino sign doc encodes signer differently from proto signer through `Web3Tx extension route -> authz limiter -> EIP-712 signature verification`.
- Invariant to test: legacy typed data must not replay across Cronos chains.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: build a two-message Cosmos tx fixture and assert ante, execution, refund, and receipt invariants after FinalizeBlock.
