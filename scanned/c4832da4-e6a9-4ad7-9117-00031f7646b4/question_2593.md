# Q2593: LegacyValidatePayloadMessages - Multiple Messages With Different Signers Pass Validation

## Question
Can an unprivileged attacker submit a legacy EIP-712/Web3Tx signed Cosmos transaction through `legacy EIP-712 message validation before Cosmos execution` while controlling `memo/timeout` and `typed data domain`, under the precondition that the chain ID/domain string is user-controlled, drive `Legacy payload validation -> fee deduction -> signature sequence increment -> message handler` in `ethereum/eip712/encoding_legacy.go::LegacyValidatePayloadMessages` so that multiple messages with different signers pass validation, violating the invariant that fee payer or granter cannot be charged outside the signed intent, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `ethereum/eip712/encoding_legacy.go::LegacyValidatePayloadMessages`
- Entrypoint: `legacy EIP-712 message validation before Cosmos execution`
- Attacker controls: `memo/timeout`, `typed data domain`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: multiple messages with different signers pass validation through `Legacy payload validation -> fee deduction -> signature sequence increment -> message handler`.
- Invariant to test: fee payer or granter cannot be charged outside the signed intent.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: force the edge value at uint64/uint256/sdk.Int boundaries and assert no smaller debit, larger refund, or supply change occurs.
