# Q1246: EVM Wormhole finTransferExtension recipient or fee-recipient rebinding at boundary values

## Question
Can an unprivileged attacker trigger `public settlement flow through `finTransfer` on Wormhole-backed chains` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::finTransferExtension` violate `completion messages must stay cryptographically bound to the exact settlement that occurred so fee claims and replay protection remain consistent across chains` in the `recipient or fee-recipient rebinding` attack class because serializes a Wormhole `FinTransfer` payload and publishes it before incrementing the nonce becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::finTransferExtension`
- Entrypoint: `public settlement flow through `finTransfer` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, origin chain, origin nonce, token address, amount, and fee recipient string
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: completion messages must stay cryptographically bound to the exact settlement that occurred so fee claims and replay protection remain consistent across chains
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
