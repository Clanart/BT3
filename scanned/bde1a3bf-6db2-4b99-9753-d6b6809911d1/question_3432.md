# Q3432: EVM Wormhole nonce progression shared Wormhole nonce can be replayed or gap-filled via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public init/deploy/log/finalize flows on Wormhole-backed chains` and then replay or reorder later bind, deploy, or metadata-consumption step so that `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::wormholeNonce usage across extensions` ends up accepting two inconsistent interpretations of the same economic event specifically around `shared Wormhole nonce can be replayed or gap-filled` under reuses one incrementing `wormholeNonce` across deploy, metadata, init, and finalize message publication, violating `Wormhole nonce progression must stay synchronized with actual published messages so a failed publish cannot be replayed or gap-filled by another message class`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::wormholeNonce usage across extensions`
- Entrypoint: `public init/deploy/log/finalize flows on Wormhole-backed chains`
- Attacker controls: message publication ordering, msg.value, and any extension reentrancy or failure mode
- Exploit idea: Target contracts that reuse one monotonic Wormhole nonce across deploy, init, metadata, and finalize messages. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: Wormhole nonce progression must stay synchronized with actual published messages so a failed publish cannot be replayed or gap-filled by another message class
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Interleave message classes and failures and assert that nonce progression remains globally unique and monotonic for emitted messages. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
