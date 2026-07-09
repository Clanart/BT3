# Q746: EVM Wormhole deployTokenExtension malicious metadata manufactures a bridge identity

## Question
Can an unprivileged attacker invoke `public deploy flow through `deployToken` on Wormhole-backed chains` with a malicious token or metadata payload so that `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::deployTokenExtension` records a deceptive asset identity that later drives deployment or claims, violating `message publication must stay synchronized with on-chain deployment state so a failed or reordered publish cannot leave a live token with no corresponding remote registration or vice versa`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::deployTokenExtension`
- Entrypoint: `public deploy flow through `deployToken` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, token string, deployed token address, decimals, and origin decimals
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs.
- Invariant to test: message publication must stay synchronized with on-chain deployment state so a failed or reordered publish cannot leave a live token with no corresponding remote registration or vice versa
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals.
