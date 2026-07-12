# Q1789: LegacyValidatePayloadMessages - Multiple Messages With Different Signers Pass Validation

## Question
Can an unprivileged attacker submit a legacy EIP-712/Web3Tx signed Cosmos transaction through `legacy EIP-712 message validation before Cosmos execution` while controlling `typed data domain` and `message signer`, under the precondition that the user signs via the legacy Web3Tx/EIP-712 route, drive `Web3Tx extension route -> authz limiter -> EIP-712 signature verification` in `ethereum/eip712/encoding_legacy.go::LegacyValidatePayloadMessages` so that multiple messages with different signers pass validation, violating the invariant that the EIP-712 signed payload must bind chain ID, signer, sequence, fees, and exact messages, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `ethereum/eip712/encoding_legacy.go::LegacyValidatePayloadMessages`
- Entrypoint: `legacy EIP-712 message validation before Cosmos execution`
- Attacker controls: `typed data domain`, `message signer`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: multiple messages with different signers pass validation through `Web3Tx extension route -> authz limiter -> EIP-712 signature verification`.
- Invariant to test: the EIP-712 signed payload must bind chain ID, signer, sequence, fees, and exact messages.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: write a focused Go unit test around the target function and assert bank supply, sender balance, nonce, code hash, logs, and receipt status before and after.
