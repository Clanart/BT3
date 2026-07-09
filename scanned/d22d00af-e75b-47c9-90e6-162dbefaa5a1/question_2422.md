# Q2422: EVM Wormhole nonce progression malicious metadata manufactures a bridge identity through cross-module drift

## Question
Can an unprivileged attacker use `public init/deploy/log/finalize flows on Wormhole-backed chains` with control over message publication ordering, msg.value, and any extension reentrancy or failure mode and desynchronize `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::wormholeNonce usage across extensions` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `malicious metadata manufactures a bridge identity` attack class because reuses one incrementing `wormholeNonce` across deploy, metadata, init, and finalize message publication, violating `Wormhole nonce progression must stay synchronized with actual published messages so a failed publish cannot be replayed or gap-filled by another message class`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::wormholeNonce usage across extensions`
- Entrypoint: `public init/deploy/log/finalize flows on Wormhole-backed chains`
- Attacker controls: message publication ordering, msg.value, and any extension reentrancy or failure mode
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: Wormhole nonce progression must stay synchronized with actual published messages so a failed publish cannot be replayed or gap-filled by another message class
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::wormholeNonce usage across extensions` and the adjacent replay-protection bookkeeping after every branch.
