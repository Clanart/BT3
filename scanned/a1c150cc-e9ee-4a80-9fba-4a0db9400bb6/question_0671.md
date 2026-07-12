# Q671: LegacyValidatePayloadMessages - Message Type Url Normalization Permits Disabled Message

## Question
Can an unprivileged attacker submit a legacy EIP-712/Web3Tx signed Cosmos transaction through `legacy EIP-712 message validation before Cosmos execution` while controlling `fee payer` and `fee amount`, under the precondition that the payload contains authz or fee delegation, drive `EIP-712 typed data generation -> LegacyEip712SigVerificationDecorator -> Cosmos message execution` in `ethereum/eip712/encoding_legacy.go::LegacyValidatePayloadMessages` so that message type URL normalization permits disabled message, violating the invariant that authz execution must not broaden what the signer approved, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `ethereum/eip712/encoding_legacy.go::LegacyValidatePayloadMessages`
- Entrypoint: `legacy EIP-712 message validation before Cosmos execution`
- Attacker controls: `fee payer`, `fee amount`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: message type URL normalization permits disabled message through `EIP-712 typed data generation -> LegacyEip712SigVerificationDecorator -> Cosmos message execution`.
- Invariant to test: authz execution must not broaden what the signer approved.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: replay the same scenario through eth_call or estimateGas and through eth_sendRawTransaction and assert the only difference is persistence.
