# Q3774: EVM Wormhole finTransferExtension captured predecessor identity can be abused for fee payout

## Question
Can an unprivileged attacker exploit asynchronous callbacks behind `public settlement flow through `finTransfer` on Wormhole-backed chains` so that `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::finTransferExtension` trusts the wrong predecessor account for fee payout or storage charging, violating `completion messages must stay cryptographically bound to the exact settlement that occurred so fee claims and replay protection remain consistent across chains`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::finTransferExtension`
- Entrypoint: `public settlement flow through `finTransfer` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, origin chain, origin nonce, token address, amount, and fee recipient string
- Exploit idea: Attack callbacks that carry caller identity as an argument across promises instead of re-reading a trusted on-chain source.
- Invariant to test: completion messages must stay cryptographically bound to the exact settlement that occurred so fee claims and replay protection remain consistent across chains
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Try nested calls and intermediary contracts and assert that callback arguments cannot redirect fee entitlement away from the authentic proof subject.
