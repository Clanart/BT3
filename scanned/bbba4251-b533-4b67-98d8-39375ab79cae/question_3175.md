# Q3175: EVM transfer payload Borsh encoding replay state keyed too narrowly for the true domain at boundary values

## Question
Can an unprivileged attacker trigger `public EVM `finTransfer` and `deployToken` signature paths` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly` violate `manual payload assembly must not admit alternative byte layouts that verify the same signature for materially different bridge actions` in the `replay state keyed too narrowly for the true domain` attack class because manually concatenates payload fields before Keccak and ECDSA recovery becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly`
- Entrypoint: `public EVM `finTransfer` and `deployToken` signature paths`
- Attacker controls: all fields concatenated into Borsh-encoded bytes including optional fee-recipient and message bytes
- Exploit idea: Look for nonce-only or bucket-only replay keys where the full economic domain includes more fields. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: manual payload assembly must not admit alternative byte layouts that verify the same signature for materially different bridge actions
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Construct distinct valid events that share the same nonce-like field and assert that settling one does not block or authorize the other incorrectly. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
