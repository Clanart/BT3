# Q167: EVM transfer payload Borsh encoding replay guard can be bypassed or consumed incorrectly

## Question
Can an unprivileged attacker settle through `public EVM `finTransfer` and `deployToken` signature paths` and make `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly` either bypass replay protection or consume it for the wrong event because of manually concatenates payload fields before Keccak and ECDSA recovery, violating `manual payload assembly must not admit alternative byte layouts that verify the same signature for materially different bridge actions`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol signed payload assembly`
- Entrypoint: `public EVM `finTransfer` and `deployToken` signature paths`
- Attacker controls: all fields concatenated into Borsh-encoded bytes including optional fee-recipient and message bytes
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains.
- Invariant to test: manual payload assembly must not admit alternative byte layouts that verify the same signature for materially different bridge actions
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used.
