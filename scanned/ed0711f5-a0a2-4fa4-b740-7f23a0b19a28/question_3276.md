# Q3276: EVM bridge recipient/message strings one inbound event spawns multiple outbound obligations

## Question
Can an unprivileged attacker settle through `public EVM init/finalize entrypoints and Wormhole extensions` and make `evm/src/omni-bridge/contracts/OmniBridge.sol recipient/message handling` both release local value and create a second valid outbound bridge obligation via serializes recipient and optional strings into signed payloads and Wormhole messages that other chains later parse as `OmniAddress` or application messages, violating `string encoding must not let empty, overlong, or non-canonical forms change who gets paid or which message downstream chains execute`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol recipient/message handling`
- Entrypoint: `public EVM init/finalize entrypoints and Wormhole extensions`
- Attacker controls: recipient string, message bytes, empty versus non-empty optional encoding, and fee-recipient string
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer.
- Invariant to test: string encoding must not let empty, overlong, or non-canonical forms change who gets paid or which message downstream chains execute
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims.
