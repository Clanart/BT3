# Q749: EVM Wormhole initTransferExtension recipient or message ambiguity

## Question
Can an unprivileged attacker supply attacker-controlled recipient or message data through `public outbound flow through `initTransfer` on Wormhole-backed chains` and make `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::initTransferExtension` encode or parse it differently than downstream chains expect via serializes a Wormhole `InitTransfer` payload and publishes it using the current nonce and supplied value, violating `outbound message publication must remain one-to-one with consumed assets and must not diverge under underpayment, overpayment, or callback/retry edge cases`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::initTransferExtension`
- Entrypoint: `public outbound flow through `initTransfer` on Wormhole-backed chains`
- Attacker controls: msg.value passed as `value`, current `wormholeNonce`, sender, token address, origin nonce, amount, fee, native fee, recipient string, and message
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message.
- Invariant to test: outbound message publication must remain one-to-one with consumed assets and must not diverge under underpayment, overpayment, or callback/retry edge cases
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages.
