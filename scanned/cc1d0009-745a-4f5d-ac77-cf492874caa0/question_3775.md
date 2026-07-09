# Q3775: EVM Wormhole initTransferExtension optional string alias changes bridge subject

## Question
Can an unprivileged attacker use empty, null, or specially-encoded strings in `public outbound flow through `initTransfer` on Wormhole-backed chains` such that `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::initTransferExtension` treats them as one semantic subject while another parser treats them as another, violating `outbound message publication must remain one-to-one with consumed assets and must not diverge under underpayment, overpayment, or callback/retry edge cases`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::initTransferExtension`
- Entrypoint: `public outbound flow through `initTransfer` on Wormhole-backed chains`
- Attacker controls: msg.value passed as `value`, current `wormholeNonce`, sender, token address, origin nonce, amount, fee, native fee, recipient string, and message
- Exploit idea: Focus on optional fee recipients, empty messages, token strings, and recipient strings.
- Invariant to test: outbound message publication must remain one-to-one with consumed assets and must not diverge under underpayment, overpayment, or callback/retry edge cases
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-parse edge-case strings across all implementations and assert a single canonical meaning for every accepted value.
