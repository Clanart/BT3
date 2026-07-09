# Q2645: EVM OmniBridge initTransfer resume-path replay or duplication

## Question
Can an unprivileged attacker make the deferred path behind `public EVM outbound transfer entrypoint` resume more than once or resume after the economic transfer was already completed because `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer` relies on increments `currentOriginNonce`, enforces `fee < amount`, collects or burns assets depending on native/custom/bridge-token branches, and forwards value to `initTransferExtension`, violating `one outbound transfer must consume exactly the assets represented by the emitted bridge event and must not let branch-specific accounting diverge from the signed or relayed payload`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer`
- Entrypoint: `public EVM outbound transfer entrypoint`
- Attacker controls: token address, amount, fee, native fee, recipient string, message bytes, and msg.value
- Exploit idea: Abuse yield/resume or asynchronous callback timing so the same pending outbound transfer is restarted after it already progressed.
- Invariant to test: one outbound transfer must consume exactly the assets represented by the emitted bridge event and must not let branch-specific accounting diverge from the signed or relayed payload
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trigger timeouts, duplicate funding, and repeated callback delivery and assert that the resumed transfer either progresses once or cleanly fails once.
