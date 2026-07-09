# Q837: EVM transfer payload Borsh encoding state update before full validation

## Question
Can an unprivileged attacker exploit `public EVM `finTransfer` and `deployToken` signature paths` so that `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly` mutates finalization state before all signature or proof checks implied by manually concatenates payload fields before Keccak and ECDSA recovery are complete, violating `manual payload assembly must not admit alternative byte layouts that verify the same signature for materially different bridge actions`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly`
- Entrypoint: `public EVM `finTransfer` and `deployToken` signature paths`
- Attacker controls: all fields concatenated into Borsh-encoded bytes including optional fee-recipient and message bytes
- Exploit idea: Look for `completed`, `finalised`, or bitmap writes that happen before every branch-specific validation step and external effect.
- Invariant to test: manual payload assembly must not admit alternative byte layouts that verify the same signature for materially different bridge actions
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force later validation or delivery to fail after replay state was consumed and assert that the transfer cannot be stranded or reopened inconsistently.
