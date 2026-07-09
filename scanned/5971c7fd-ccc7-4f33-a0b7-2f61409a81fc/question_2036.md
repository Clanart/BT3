# Q2036: EVM OmniBridge logMetadata remote publication drifts from local deployment state

## Question
Can an unprivileged attacker exploit `public EVM metadata logging entrypoint` so that `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata` publishes a deploy or metadata message that no longer matches local token state because of reads metadata from an arbitrary ERC-20-like token, passes it to `logMetadataExtension`, and emits a `LogMetadata` event, violating `metadata logging must not let malicious token contracts create bridge-side asset identities or deployment proofs that can later mint or map the wrong asset`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata`
- Entrypoint: `public EVM metadata logging entrypoint`
- Attacker controls: token contract address, token-reported `name`, `symbol`, `decimals`, and msg.value for extensions
- Exploit idea: Focus on message publication before/after nonce increments, mapping writes, or external deploy steps.
- Invariant to test: metadata logging must not let malicious token contracts create bridge-side asset identities or deployment proofs that can later mint or map the wrong asset
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Fail each external step independently and assert that every published message corresponds to exactly one deployed and bindable local token.
