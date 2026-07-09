# Q1410: EVM Wormhole initTransferExtension fee and principal split divergence

## Question
Can an unprivileged attacker enter through `public outbound flow through `initTransfer` on Wormhole-backed chains` with crafted amount, fee, or native-fee inputs and make `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::initTransferExtension` use inconsistent fee and principal values across serializes a Wormhole `InitTransfer` payload and publishes it using the current nonce and supplied value, violating `outbound message publication must remain one-to-one with consumed assets and must not diverge under underpayment, overpayment, or callback/retry edge cases`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::initTransferExtension`
- Entrypoint: `public outbound flow through `initTransfer` on Wormhole-backed chains`
- Attacker controls: msg.value passed as `value`, current `wormholeNonce`, sender, token address, origin nonce, amount, fee, native fee, recipient string, and message
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing.
- Invariant to test: outbound message publication must remain one-to-one with consumed assets and must not diverge under underpayment, overpayment, or callback/retry edge cases
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value.
