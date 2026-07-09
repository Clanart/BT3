# Q735: EVM OmniBridge logMetadata1155 malicious metadata manufactures a bridge identity

## Question
Can an unprivileged attacker invoke `public EVM metadata logging for ERC-1155 wrappers` with a malicious token or metadata payload so that `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata1155` records a deceptive asset identity that later drives deployment or claims, violating `the deterministic alias for an ERC-1155 asset must be collision-resistant and must never point two distinct underlying assets at one bridge identity`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata1155`
- Entrypoint: `public EVM metadata logging for ERC-1155 wrappers`
- Attacker controls: ERC-1155 contract address, token id, deterministic-token alias, and msg.value for extensions
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs.
- Invariant to test: the deterministic alias for an ERC-1155 asset must be collision-resistant and must never point two distinct underlying assets at one bridge identity
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals.
