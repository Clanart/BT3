# Q2992: EVM bridge recipient/message strings recipient or fee-recipient rebinding through cross-module drift

## Question
Can an unprivileged attacker use `public EVM init/finalize entrypoints and Wormhole extensions` with control over recipient string, message bytes, empty versus non-empty optional encoding, and fee-recipient string and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol recipient/message handling` from the adjacent the next module that consumes the same asset or transfer id that shares the same asset, nonce, proof subject, or mapping specifically in the `recipient or fee-recipient rebinding` attack class because serializes recipient and optional strings into signed payloads and Wormhole messages that other chains later parse as `OmniAddress` or application messages, violating `string encoding must not let empty, overlong, or non-canonical forms change who gets paid or which message downstream chains execute`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol recipient/message handling`
- Entrypoint: `public EVM init/finalize entrypoints and Wormhole extensions`
- Attacker controls: recipient string, message bytes, empty versus non-empty optional encoding, and fee-recipient string
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Focus on drift between this module and the adjacent the next module that consumes the same asset or transfer id.
- Invariant to test: string encoding must not let empty, overlong, or non-canonical forms change who gets paid or which message downstream chains execute
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol recipient/message handling` and the adjacent the next module that consumes the same asset or transfer id after every branch.
