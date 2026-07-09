# Q1301: EVM custom-minter bridge path resume-path replay or duplication at boundary values

## Question
Can an unprivileged attacker trigger `public `initTransfer` and `finTransfer` when `customMinters[token] != 0`` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches` violate `custom-minter branches must not let a token escape custody or mint semantics that the bridge assumes for replay protection and accounting` in the `resume-path replay or duplication` attack class because delegates burn and mint semantics to an external custom minter instead of standard bridge-token logic becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches`
- Entrypoint: `public `initTransfer` and `finTransfer` when `customMinters[token] != 0``
- Attacker controls: token address, custom-minter registration state, amount, and recipient
- Exploit idea: Abuse yield/resume or asynchronous callback timing so the same pending outbound transfer is restarted after it already progressed. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: custom-minter branches must not let a token escape custody or mint semantics that the bridge assumes for replay protection and accounting
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trigger timeouts, duplicate funding, and repeated callback delivery and assert that the resumed transfer either progresses once or cleanly fails once. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
