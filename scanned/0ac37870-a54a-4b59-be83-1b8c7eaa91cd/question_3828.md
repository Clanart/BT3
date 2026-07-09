# Q3828: EVM Wormhole nonce progression sequence or consistency semantics ignored

## Question
Can an unprivileged attacker exploit `public init/deploy/log/finalize flows on Wormhole-backed chains` so that `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::wormholeNonce usage across extensions` ignores Wormhole ordering or consistency assumptions that should distinguish one event from another, violating `Wormhole nonce progression must stay synchronized with actual published messages so a failed publish cannot be replayed or gap-filled by another message class`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::wormholeNonce usage across extensions`
- Entrypoint: `public init/deploy/log/finalize flows on Wormhole-backed chains`
- Attacker controls: message publication ordering, msg.value, and any extension reentrancy or failure mode
- Exploit idea: Target paths that validate guardianship but do not use VAA sequence, timestamp, or consistency metadata to couple events.
- Invariant to test: Wormhole nonce progression must stay synchronized with actual published messages so a failed publish cannot be replayed or gap-filled by another message class
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay stale but valid VAAs around later state changes and assert that the bridge still enforces exact-event uniqueness.
