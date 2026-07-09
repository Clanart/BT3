# Q3237: EVM Wormhole finTransferExtension same fee collectible twice

## Question
Can an unprivileged attacker reach `public settlement flow through `finTransfer` on Wormhole-backed chains` and make `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::finTransferExtension` remove or preserve fee-bearing state in a way that allows the same fee to be claimed twice, violating `completion messages must stay cryptographically bound to the exact settlement that occurred so fee claims and replay protection remain consistent across chains`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::finTransferExtension`
- Entrypoint: `public settlement flow through `finTransfer` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, origin chain, origin nonce, token address, amount, and fee recipient string
- Exploit idea: Target pending-transfer cleanup, fast-transfer removal, and replay protection around fee-claim proofs.
- Invariant to test: completion messages must stay cryptographically bound to the exact settlement that occurred so fee claims and replay protection remain consistent across chains
- Expected Immunefi impact: Balance manipulation
- Fast validation: Claim once, then replay or mutate non-economic proof fields and assert that no second claim succeeds or blocks unrelated transfers.
