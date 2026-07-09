# Q3715: EVM transfer payload Borsh encoding hashed or padded seed collision at boundary values

## Question
Can an unprivileged attacker trigger `public EVM `finTransfer` and `deployToken` signature paths` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly` violate `manual payload assembly must not admit alternative byte layouts that verify the same signature for materially different bridge actions` in the `hashed or padded seed collision` attack class because manually concatenates payload fields before Keccak and ECDSA recovery becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly`
- Entrypoint: `public EVM `finTransfer` and `deployToken` signature paths`
- Attacker controls: all fields concatenated into Borsh-encoded bytes including optional fee-recipient and message bytes
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: manual payload assembly must not admit alternative byte layouts that verify the same signature for materially different bridge actions
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
