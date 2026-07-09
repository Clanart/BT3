# Q1979: EVM transfer payload Borsh encoding recipient or fee-recipient rebinding at boundary values

## Question
Can an unprivileged attacker trigger `public EVM `finTransfer` and `deployToken` signature paths` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly` violate `manual payload assembly must not admit alternative byte layouts that verify the same signature for materially different bridge actions` in the `recipient or fee-recipient rebinding` attack class because manually concatenates payload fields before Keccak and ECDSA recovery becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly`
- Entrypoint: `public EVM `finTransfer` and `deployToken` signature paths`
- Attacker controls: all fields concatenated into Borsh-encoded bytes including optional fee-recipient and message bytes
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: manual payload assembly must not admit alternative byte layouts that verify the same signature for materially different bridge actions
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
