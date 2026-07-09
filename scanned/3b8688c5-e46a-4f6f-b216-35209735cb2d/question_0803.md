# Q803: EVM custom-minter bridge path resume-path replay or duplication

## Question
Can an unprivileged attacker make the deferred path behind `public `initTransfer` and `finTransfer` when `customMinters[token] != 0`` resume more than once or resume after the economic transfer was already completed because `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches` relies on delegates burn and mint semantics to an external custom minter instead of standard bridge-token logic, violating `custom-minter branches must not let a token escape custody or mint semantics that the bridge assumes for replay protection and accounting`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches`
- Entrypoint: `public `initTransfer` and `finTransfer` when `customMinters[token] != 0``
- Attacker controls: token address, custom-minter registration state, amount, and recipient
- Exploit idea: Abuse yield/resume or asynchronous callback timing so the same pending outbound transfer is restarted after it already progressed.
- Invariant to test: custom-minter branches must not let a token escape custody or mint semantics that the bridge assumes for replay protection and accounting
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trigger timeouts, duplicate funding, and repeated callback delivery and assert that the resumed transfer either progresses once or cleanly fails once.
