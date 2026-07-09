# Q3028: EVM transfer payload Borsh encoding replay state keyed too narrowly for the true domain through cross-module drift

## Question
Can an unprivileged attacker use `public EVM `finTransfer` and `deployToken` signature paths` with control over all fields concatenated into Borsh-encoded bytes including optional fee-recipient and message bytes and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `replay state keyed too narrowly for the true domain` attack class because manually concatenates payload fields before Keccak and ECDSA recovery, violating `manual payload assembly must not admit alternative byte layouts that verify the same signature for materially different bridge actions`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly`
- Entrypoint: `public EVM `finTransfer` and `deployToken` signature paths`
- Attacker controls: all fields concatenated into Borsh-encoded bytes including optional fee-recipient and message bytes
- Exploit idea: Look for nonce-only or bucket-only replay keys where the full economic domain includes more fields. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: manual payload assembly must not admit alternative byte layouts that verify the same signature for materially different bridge actions
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Construct distinct valid events that share the same nonce-like field and assert that settling one does not block or authorize the other incorrectly. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly` and the adjacent replay-protection bookkeeping after every branch.
