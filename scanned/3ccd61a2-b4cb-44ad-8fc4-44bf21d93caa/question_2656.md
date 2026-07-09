# Q2656: EVM Wormhole finTransferExtension fee recipient can be substituted or reclaimed by attacker

## Question
Can an unprivileged attacker use `public settlement flow through `finTransfer` on Wormhole-backed chains` to make `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::finTransferExtension` route a legitimate fee to the wrong account because of serializes a Wormhole `FinTransfer` payload and publishes it before incrementing the nonce, violating `completion messages must stay cryptographically bound to the exact settlement that occurred so fee claims and replay protection remain consistent across chains`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::finTransferExtension`
- Entrypoint: `public settlement flow through `finTransfer` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, origin chain, origin nonce, token address, amount, and fee recipient string
- Exploit idea: Target optional fee-recipient fields, predecessor-captured identities, and relayer substitution on fast paths.
- Invariant to test: completion messages must stay cryptographically bound to the exact settlement that occurred so fee claims and replay protection remain consistent across chains
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Settle and claim with varied fee-recipient encodings and assert that only the intended recipient can ever collect that fee.
