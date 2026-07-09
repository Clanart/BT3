# Q3310: EVM transfer payload Borsh encoding hashed or padded seed collision

## Question
Can an unprivileged attacker reach `public EVM `finTransfer` and `deployToken` signature paths` with overlong or adversarial token identifiers and make `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly` derive the same local seed or salt for two remote assets because of manually concatenates payload fields before Keccak and ECDSA recovery, violating `manual payload assembly must not admit alternative byte layouts that verify the same signature for materially different bridge actions`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly`
- Entrypoint: `public EVM `finTransfer` and `deployToken` signature paths`
- Attacker controls: all fields concatenated into Borsh-encoded bytes including optional fee-recipient and message bytes
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation.
- Invariant to test: manual payload assembly must not admit alternative byte layouts that verify the same signature for materially different bridge actions
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity.
