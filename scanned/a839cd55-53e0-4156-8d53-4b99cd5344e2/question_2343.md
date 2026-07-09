# Q2343: EVM OmniBridge initTransfer fee and principal split divergence through cross-module drift

## Question
Can an unprivileged attacker use `public EVM outbound transfer entrypoint` with control over token address, amount, fee, native fee, recipient string, message bytes, and msg.value and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `fee and principal split divergence` attack class because increments `currentOriginNonce`, enforces `fee < amount`, collects or burns assets depending on native/custom/bridge-token branches, and forwards value to `initTransferExtension`, violating `one outbound transfer must consume exactly the assets represented by the emitted bridge event and must not let branch-specific accounting diverge from the signed or relayed payload`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer`
- Entrypoint: `public EVM outbound transfer entrypoint`
- Attacker controls: token address, amount, fee, native fee, recipient string, message bytes, and msg.value
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: one outbound transfer must consume exactly the assets represented by the emitted bridge event and must not let branch-specific accounting diverge from the signed or relayed payload
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer` and the adjacent replay-protection bookkeeping after every branch.
