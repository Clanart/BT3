# Q151: EVM Wormhole nonce progression origin and destination nonce desynchronization

## Question
Can an unprivileged attacker enter through `public init/deploy/log/finalize flows on Wormhole-backed chains` with control over message publication ordering, msg.value, and any extension reentrancy or failure mode and make `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::wormholeNonce usage across extensions` advance or reuse bridge nonces inconsistently with reuses one incrementing `wormholeNonce` across deploy, metadata, init, and finalize message publication, so that one economic transfer can be emitted, resumed, or signed under multiple identifiers, violating `Wormhole nonce progression must stay synchronized with actual published messages so a failed publish cannot be replayed or gap-filled by another message class`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::wormholeNonce usage across extensions`
- Entrypoint: `public init/deploy/log/finalize flows on Wormhole-backed chains`
- Attacker controls: message publication ordering, msg.value, and any extension reentrancy or failure mode
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity.
- Invariant to test: Wormhole nonce progression must stay synchronized with actual published messages so a failed publish cannot be replayed or gap-filled by another message class
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages.
