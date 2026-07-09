# Q2948: EVM Wormhole deployTokenExtension shared Wormhole nonce can be replayed or gap-filled through cross-module drift

## Question
Can an unprivileged attacker use `public deploy flow through `deployToken` on Wormhole-backed chains` with control over msg.value, current `wormholeNonce`, token string, deployed token address, decimals, and origin decimals and desynchronize `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::deployTokenExtension` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `shared Wormhole nonce can be replayed or gap-filled` attack class because serializes a Wormhole `DeployToken` payload and publishes it with the current nonce before incrementing `wormholeNonce`, violating `message publication must stay synchronized with on-chain deployment state so a failed or reordered publish cannot leave a live token with no corresponding remote registration or vice versa`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::deployTokenExtension`
- Entrypoint: `public deploy flow through `deployToken` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, token string, deployed token address, decimals, and origin decimals
- Exploit idea: Target contracts that reuse one monotonic Wormhole nonce across deploy, init, metadata, and finalize messages. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: message publication must stay synchronized with on-chain deployment state so a failed or reordered publish cannot leave a live token with no corresponding remote registration or vice versa
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Interleave message classes and failures and assert that nonce progression remains globally unique and monotonic for emitted messages. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::deployTokenExtension` and the adjacent replay-protection bookkeeping after every branch.
