# Q1878: EVM OmniBridge logMetadata fake bridge-controlled token accepted as canonical at boundary values

## Question
Can an unprivileged attacker trigger `public EVM metadata logging entrypoint` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata` violate `metadata logging must not let malicious token contracts create bridge-side asset identities or deployment proofs that can later mint or map the wrong asset` in the `fake bridge-controlled token accepted as canonical` attack class because reads metadata from an arbitrary ERC-20-like token, passes it to `logMetadataExtension`, and emits a `LogMetadata` event becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata`
- Entrypoint: `public EVM metadata logging entrypoint`
- Attacker controls: token contract address, token-reported `name`, `symbol`, `decimals`, and msg.value for extensions
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: metadata logging must not let malicious token contracts create bridge-side asset identities or deployment proofs that can later mint or map the wrong asset
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
