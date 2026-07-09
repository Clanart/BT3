# Q2574: EVM Wormhole nonce progression malicious metadata manufactures a bridge identity at boundary values

## Question
Can an unprivileged attacker trigger `public init/deploy/log/finalize flows on Wormhole-backed chains` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::wormholeNonce usage across extensions` violate `Wormhole nonce progression must stay synchronized with actual published messages so a failed publish cannot be replayed or gap-filled by another message class` in the `malicious metadata manufactures a bridge identity` attack class because reuses one incrementing `wormholeNonce` across deploy, metadata, init, and finalize message publication becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::wormholeNonce usage across extensions`
- Entrypoint: `public init/deploy/log/finalize flows on Wormhole-backed chains`
- Attacker controls: message publication ordering, msg.value, and any extension reentrancy or failure mode
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: Wormhole nonce progression must stay synchronized with actual published messages so a failed publish cannot be replayed or gap-filled by another message class
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
