# Q2492: EVM OmniBridge logMetadata remote publication drifts from local deployment state at boundary values

## Question
Can an unprivileged attacker trigger `public EVM metadata logging entrypoint` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata` violate `metadata logging must not let malicious token contracts create bridge-side asset identities or deployment proofs that can later mint or map the wrong asset` in the `remote publication drifts from local deployment state` attack class because reads metadata from an arbitrary ERC-20-like token, passes it to `logMetadataExtension`, and emits a `LogMetadata` event becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata`
- Entrypoint: `public EVM metadata logging entrypoint`
- Attacker controls: token contract address, token-reported `name`, `symbol`, `decimals`, and msg.value for extensions
- Exploit idea: Focus on message publication before/after nonce increments, mapping writes, or external deploy steps. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: metadata logging must not let malicious token contracts create bridge-side asset identities or deployment proofs that can later mint or map the wrong asset
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Fail each external step independently and assert that every published message corresponds to exactly one deployed and bindable local token. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
