# Q124: EVM bridge recipient/message strings recipient or message ambiguity

## Question
Can an unprivileged attacker supply attacker-controlled recipient or message data through `public EVM init/finalize entrypoints and Wormhole extensions` and make `evm/src/omni-bridge/contracts/OmniBridge.sol recipient/message handling` encode or parse it differently than downstream chains expect via serializes recipient and optional strings into signed payloads and Wormhole messages that other chains later parse as `OmniAddress` or application messages, violating `string encoding must not let empty, overlong, or non-canonical forms change who gets paid or which message downstream chains execute`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol recipient/message handling`
- Entrypoint: `public EVM init/finalize entrypoints and Wormhole extensions`
- Attacker controls: recipient string, message bytes, empty versus non-empty optional encoding, and fee-recipient string
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message.
- Invariant to test: string encoding must not let empty, overlong, or non-canonical forms change who gets paid or which message downstream chains execute
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages.
