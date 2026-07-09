# Q1398: EVM OmniBridge initTransfer recipient or message ambiguity

## Question
Can an unprivileged attacker supply attacker-controlled recipient or message data through `public EVM outbound transfer entrypoint` and make `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer` encode or parse it differently than downstream chains expect via increments `currentOriginNonce`, enforces `fee < amount`, collects or burns assets depending on native/custom/bridge-token branches, and forwards value to `initTransferExtension`, violating `one outbound transfer must consume exactly the assets represented by the emitted bridge event and must not let branch-specific accounting diverge from the signed or relayed payload`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer`
- Entrypoint: `public EVM outbound transfer entrypoint`
- Attacker controls: token address, amount, fee, native fee, recipient string, message bytes, and msg.value
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message.
- Invariant to test: one outbound transfer must consume exactly the assets represented by the emitted bridge event and must not let branch-specific accounting diverge from the signed or relayed payload
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages.
