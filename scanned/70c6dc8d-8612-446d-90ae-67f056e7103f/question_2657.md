# Q2657: EVM Wormhole initTransferExtension same fee collectible twice

## Question
Can an unprivileged attacker reach `public outbound flow through `initTransfer` on Wormhole-backed chains` and make `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::initTransferExtension` remove or preserve fee-bearing state in a way that allows the same fee to be claimed twice, violating `outbound message publication must remain one-to-one with consumed assets and must not diverge under underpayment, overpayment, or callback/retry edge cases`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::initTransferExtension`
- Entrypoint: `public outbound flow through `initTransfer` on Wormhole-backed chains`
- Attacker controls: msg.value passed as `value`, current `wormholeNonce`, sender, token address, origin nonce, amount, fee, native fee, recipient string, and message
- Exploit idea: Target pending-transfer cleanup, fast-transfer removal, and replay protection around fee-claim proofs.
- Invariant to test: outbound message publication must remain one-to-one with consumed assets and must not diverge under underpayment, overpayment, or callback/retry edge cases
- Expected Immunefi impact: Balance manipulation
- Fast validation: Claim once, then replay or mutate non-economic proof fields and assert that no second claim succeeds or blocks unrelated transfers.
