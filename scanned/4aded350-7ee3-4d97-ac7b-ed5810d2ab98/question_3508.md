# Q3508: EVM Wormhole initTransferExtension captured predecessor identity can be abused for fee payout through cross-module drift

## Question
Can an unprivileged attacker use `public outbound flow through `initTransfer` on Wormhole-backed chains` with control over msg.value passed as `value`, current `wormholeNonce`, sender, token address, origin nonce, amount, fee, native fee, recipient string, and message and desynchronize `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::initTransferExtension` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `captured predecessor identity can be abused for fee payout` attack class because serializes a Wormhole `InitTransfer` payload and publishes it using the current nonce and supplied value, violating `outbound message publication must remain one-to-one with consumed assets and must not diverge under underpayment, overpayment, or callback/retry edge cases`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::initTransferExtension`
- Entrypoint: `public outbound flow through `initTransfer` on Wormhole-backed chains`
- Attacker controls: msg.value passed as `value`, current `wormholeNonce`, sender, token address, origin nonce, amount, fee, native fee, recipient string, and message
- Exploit idea: Attack callbacks that carry caller identity as an argument across promises instead of re-reading a trusted on-chain source. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: outbound message publication must remain one-to-one with consumed assets and must not diverge under underpayment, overpayment, or callback/retry edge cases
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Try nested calls and intermediary contracts and assert that callback arguments cannot redirect fee entitlement away from the authentic proof subject. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::initTransferExtension` and the adjacent replay-protection bookkeeping after every branch.
