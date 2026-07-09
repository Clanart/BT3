# Q1818: EVM transfer payload Borsh encoding recipient or fee-recipient rebinding through cross-module drift

## Question
Can an unprivileged attacker use `public EVM `finTransfer` and `deployToken` signature paths` with control over all fields concatenated into Borsh-encoded bytes including optional fee-recipient and message bytes and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `recipient or fee-recipient rebinding` attack class because manually concatenates payload fields before Keccak and ECDSA recovery, violating `manual payload assembly must not admit alternative byte layouts that verify the same signature for materially different bridge actions`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly`
- Entrypoint: `public EVM `finTransfer` and `deployToken` signature paths`
- Attacker controls: all fields concatenated into Borsh-encoded bytes including optional fee-recipient and message bytes
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: manual payload assembly must not admit alternative byte layouts that verify the same signature for materially different bridge actions
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly` and the adjacent replay-protection bookkeeping after every branch.
