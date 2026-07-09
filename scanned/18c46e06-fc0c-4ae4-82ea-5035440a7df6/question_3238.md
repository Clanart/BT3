# Q3238: EVM Wormhole initTransferExtension captured predecessor identity can be abused for fee payout

## Question
Can an unprivileged attacker exploit asynchronous callbacks behind `public outbound flow through `initTransfer` on Wormhole-backed chains` so that `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::initTransferExtension` trusts the wrong predecessor account for fee payout or storage charging, violating `outbound message publication must remain one-to-one with consumed assets and must not diverge under underpayment, overpayment, or callback/retry edge cases`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::initTransferExtension`
- Entrypoint: `public outbound flow through `initTransfer` on Wormhole-backed chains`
- Attacker controls: msg.value passed as `value`, current `wormholeNonce`, sender, token address, origin nonce, amount, fee, native fee, recipient string, and message
- Exploit idea: Attack callbacks that carry caller identity as an argument across promises instead of re-reading a trusted on-chain source.
- Invariant to test: outbound message publication must remain one-to-one with consumed assets and must not diverge under underpayment, overpayment, or callback/retry edge cases
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Try nested calls and intermediary contracts and assert that callback arguments cannot redirect fee entitlement away from the authentic proof subject.
