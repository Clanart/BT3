# Q2939: EVM OmniBridge initTransfer resume-path replay or duplication through cross-module drift

## Question
Can an unprivileged attacker use `public EVM outbound transfer entrypoint` with control over token address, amount, fee, native fee, recipient string, message bytes, and msg.value and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `resume-path replay or duplication` attack class because increments `currentOriginNonce`, enforces `fee < amount`, collects or burns assets depending on native/custom/bridge-token branches, and forwards value to `initTransferExtension`, violating `one outbound transfer must consume exactly the assets represented by the emitted bridge event and must not let branch-specific accounting diverge from the signed or relayed payload`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer`
- Entrypoint: `public EVM outbound transfer entrypoint`
- Attacker controls: token address, amount, fee, native fee, recipient string, message bytes, and msg.value
- Exploit idea: Abuse yield/resume or asynchronous callback timing so the same pending outbound transfer is restarted after it already progressed. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: one outbound transfer must consume exactly the assets represented by the emitted bridge event and must not let branch-specific accounting diverge from the signed or relayed payload
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trigger timeouts, duplicate funding, and repeated callback delivery and assert that the resumed transfer either progresses once or cleanly fails once. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer` and the adjacent replay-protection bookkeeping after every branch.
