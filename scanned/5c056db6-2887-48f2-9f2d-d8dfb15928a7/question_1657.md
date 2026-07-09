# Q1657: EVM transfer payload Borsh encoding recipient or fee-recipient rebinding via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public EVM `finTransfer` and `deployToken` signature paths` and then replay or reorder later fee-claim proof submission so that `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly` ends up accepting two inconsistent interpretations of the same economic event specifically around `recipient or fee-recipient rebinding` under manually concatenates payload fields before Keccak and ECDSA recovery, violating `manual payload assembly must not admit alternative byte layouts that verify the same signature for materially different bridge actions`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly`
- Entrypoint: `public EVM `finTransfer` and `deployToken` signature paths`
- Attacker controls: all fields concatenated into Borsh-encoded bytes including optional fee-recipient and message bytes
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: manual payload assembly must not admit alternative byte layouts that verify the same signature for materially different bridge actions
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
