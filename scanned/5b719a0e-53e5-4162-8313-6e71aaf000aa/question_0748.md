# Q748: EVM Wormhole finTransferExtension recipient or fee-recipient rebinding

## Question
Can an unprivileged attacker submit data through `public settlement flow through `finTransfer` on Wormhole-backed chains` that makes `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::finTransferExtension` settle principal to one party but authorize fee claim or callback routing for another due to serializes a Wormhole `FinTransfer` payload and publishes it before incrementing the nonce, violating `completion messages must stay cryptographically bound to the exact settlement that occurred so fee claims and replay protection remain consistent across chains`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::finTransferExtension`
- Entrypoint: `public settlement flow through `finTransfer` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, origin chain, origin nonce, token address, amount, and fee recipient string
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities.
- Invariant to test: completion messages must stay cryptographically bound to the exact settlement that occurred so fee claims and replay protection remain consistent across chains
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple.
