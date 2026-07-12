# Q2983: LegacyValidatePayloadMessages - Multiple Messages With Different Signers Pass Validation

## Question
Can an unprivileged attacker submit a legacy EIP-712/Web3Tx signed Cosmos transaction through `legacy EIP-712 message validation before Cosmos execution` while controlling `chain ID string` and `authz payload`, under the precondition that the payload contains authz or fee delegation, drive `Web3Tx extension route -> authz limiter -> EIP-712 signature verification` in `ethereum/eip712/encoding_legacy.go::LegacyValidatePayloadMessages` so that multiple messages with different signers pass validation, violating the invariant that authz execution must not broaden what the signer approved, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `ethereum/eip712/encoding_legacy.go::LegacyValidatePayloadMessages`
- Entrypoint: `legacy EIP-712 message validation before Cosmos execution`
- Attacker controls: `chain ID string`, `authz payload`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: multiple messages with different signers pass validation through `Web3Tx extension route -> authz limiter -> EIP-712 signature verification`.
- Invariant to test: authz execution must not broaden what the signer approved.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: run a local integration test that submits the crafted raw tx through JSON-RPC and compares committed state with direct keeper queries.
