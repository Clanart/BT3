# Q3765: EVM OmniBridge logMetadata ABI version switch changes metadata identity

## Question
Can an unprivileged attacker trigger `public EVM metadata logging entrypoint` so that `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata` chooses the wrong ABI branch for metadata parsing because of reads metadata from an arbitrary ERC-20-like token, passes it to `logMetadataExtension`, and emits a `LogMetadata` event, violating `metadata logging must not let malicious token contracts create bridge-side asset identities or deployment proofs that can later mint or map the wrong asset`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata`
- Entrypoint: `public EVM metadata logging entrypoint`
- Attacker controls: token contract address, token-reported `name`, `symbol`, `decimals`, and msg.value for extensions
- Exploit idea: Target old-style versus new-style token metadata return shapes and zero-length special cases.
- Invariant to test: metadata logging must not let malicious token contracts create bridge-side asset identities or deployment proofs that can later mint or map the wrong asset
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Return ambiguous ABI payloads and assert that the bridge either rejects them or derives the exact intended metadata once.
