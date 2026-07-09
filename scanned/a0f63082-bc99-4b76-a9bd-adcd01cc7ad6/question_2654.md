# Q2654: EVM Wormhole deployTokenExtension shared Wormhole nonce can be replayed or gap-filled

## Question
Can an unprivileged attacker drive `public deploy flow through `deployToken` on Wormhole-backed chains` so that `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::deployTokenExtension` leaves exploitable gaps or reuse in the shared Wormhole nonce space across message classes, violating `message publication must stay synchronized with on-chain deployment state so a failed or reordered publish cannot leave a live token with no corresponding remote registration or vice versa`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::deployTokenExtension`
- Entrypoint: `public deploy flow through `deployToken` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, token string, deployed token address, decimals, and origin decimals
- Exploit idea: Target contracts that reuse one monotonic Wormhole nonce across deploy, init, metadata, and finalize messages.
- Invariant to test: message publication must stay synchronized with on-chain deployment state so a failed or reordered publish cannot leave a live token with no corresponding remote registration or vice versa
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Interleave message classes and failures and assert that nonce progression remains globally unique and monotonic for emitted messages.
