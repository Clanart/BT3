# Q2051: EVM Wormhole initTransferExtension resume-path replay or duplication

## Question
Can an unprivileged attacker make the deferred path behind `public outbound flow through `initTransfer` on Wormhole-backed chains` resume more than once or resume after the economic transfer was already completed because `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::initTransferExtension` relies on serializes a Wormhole `InitTransfer` payload and publishes it using the current nonce and supplied value, violating `outbound message publication must remain one-to-one with consumed assets and must not diverge under underpayment, overpayment, or callback/retry edge cases`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::initTransferExtension`
- Entrypoint: `public outbound flow through `initTransfer` on Wormhole-backed chains`
- Attacker controls: msg.value passed as `value`, current `wormholeNonce`, sender, token address, origin nonce, amount, fee, native fee, recipient string, and message
- Exploit idea: Abuse yield/resume or asynchronous callback timing so the same pending outbound transfer is restarted after it already progressed.
- Invariant to test: outbound message publication must remain one-to-one with consumed assets and must not diverge under underpayment, overpayment, or callback/retry edge cases
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trigger timeouts, duplicate funding, and repeated callback delivery and assert that the resumed transfer either progresses once or cleanly fails once.
