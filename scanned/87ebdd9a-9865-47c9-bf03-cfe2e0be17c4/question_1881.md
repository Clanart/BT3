# Q1881: EVM OmniBridge initTransfer recipient or message ambiguity at boundary values

## Question
Can an unprivileged attacker trigger `public EVM outbound transfer entrypoint` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer` violate `one outbound transfer must consume exactly the assets represented by the emitted bridge event and must not let branch-specific accounting diverge from the signed or relayed payload` in the `recipient or message ambiguity` attack class because increments `currentOriginNonce`, enforces `fee < amount`, collects or burns assets depending on native/custom/bridge-token branches, and forwards value to `initTransferExtension` becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer`
- Entrypoint: `public EVM outbound transfer entrypoint`
- Attacker controls: token address, amount, fee, native fee, recipient string, message bytes, and msg.value
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: one outbound transfer must consume exactly the assets represented by the emitted bridge event and must not let branch-specific accounting diverge from the signed or relayed payload
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
