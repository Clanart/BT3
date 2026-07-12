# Q155: LegacyValidatePayloadMessages - Empty Message List Reaches Signature Verification

## Question
Can an unprivileged attacker submit a legacy EIP-712/Web3Tx signed Cosmos transaction through `legacy EIP-712 message validation before Cosmos execution` while controlling `fee amount` and `chain ID string`, under the precondition that the transaction contains multiple Cosmos messages, drive `EIP-712 typed data generation -> LegacyEip712SigVerificationDecorator -> Cosmos message execution` in `ethereum/eip712/encoding_legacy.go::LegacyValidatePayloadMessages` so that empty message list reaches signature verification, violating the invariant that legacy typed data must not replay across Cronos chains, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `ethereum/eip712/encoding_legacy.go::LegacyValidatePayloadMessages`
- Entrypoint: `legacy EIP-712 message validation before Cosmos execution`
- Attacker controls: `fee amount`, `chain ID string`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: empty message list reaches signature verification through `EIP-712 typed data generation -> LegacyEip712SigVerificationDecorator -> Cosmos message execution`.
- Invariant to test: legacy typed data must not replay across Cronos chains.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: build a two-message Cosmos tx fixture and assert ante, execution, refund, and receipt invariants after FinalizeBlock.
