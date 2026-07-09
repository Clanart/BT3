# Q912: EVM Wormhole deployTokenExtension malicious metadata manufactures a bridge identity via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public deploy flow through `deployToken` on Wormhole-backed chains` and then replay or reorder later callback or refund resolution so that `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::deployTokenExtension` ends up accepting two inconsistent interpretations of the same economic event specifically around `malicious metadata manufactures a bridge identity` under serializes a Wormhole `DeployToken` payload and publishes it with the current nonce before incrementing `wormholeNonce`, violating `message publication must stay synchronized with on-chain deployment state so a failed or reordered publish cannot leave a live token with no corresponding remote registration or vice versa`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::deployTokenExtension`
- Entrypoint: `public deploy flow through `deployToken` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, token string, deployed token address, decimals, and origin decimals
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: message publication must stay synchronized with on-chain deployment state so a failed or reordered publish cannot leave a live token with no corresponding remote registration or vice versa
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
