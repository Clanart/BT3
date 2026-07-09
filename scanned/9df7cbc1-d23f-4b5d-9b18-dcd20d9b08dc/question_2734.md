# Q2734: EVM transfer payload Borsh encoding replay state keyed too narrowly for the true domain

## Question
Can an unprivileged attacker exploit `public EVM `finTransfer` and `deployToken` signature paths` so that `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly` treats two events from different chains, assets, or message classes as sharing one replay slot because of manually concatenates payload fields before Keccak and ECDSA recovery, violating `manual payload assembly must not admit alternative byte layouts that verify the same signature for materially different bridge actions`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly`
- Entrypoint: `public EVM `finTransfer` and `deployToken` signature paths`
- Attacker controls: all fields concatenated into Borsh-encoded bytes including optional fee-recipient and message bytes
- Exploit idea: Look for nonce-only or bucket-only replay keys where the full economic domain includes more fields.
- Invariant to test: manual payload assembly must not admit alternative byte layouts that verify the same signature for materially different bridge actions
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Construct distinct valid events that share the same nonce-like field and assert that settling one does not block or authorize the other incorrectly.
