# Q2506: EVM Wormhole finTransferExtension final settlement and later fee claim can diverge at boundary values

## Question
Can an unprivileged attacker trigger `public settlement flow through `finTransfer` on Wormhole-backed chains` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::finTransferExtension` violate `completion messages must stay cryptographically bound to the exact settlement that occurred so fee claims and replay protection remain consistent across chains` in the `final settlement and later fee claim can diverge` attack class because serializes a Wormhole `FinTransfer` payload and publishes it before incrementing the nonce becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::finTransferExtension`
- Entrypoint: `public settlement flow through `finTransfer` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, origin chain, origin nonce, token address, amount, and fee recipient string
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: completion messages must stay cryptographically bound to the exact settlement that occurred so fee claims and replay protection remain consistent across chains
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
