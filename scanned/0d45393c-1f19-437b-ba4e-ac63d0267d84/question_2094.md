# Q2094: EVM bridge recipient/message strings state update before full validation

## Question
Can an unprivileged attacker exploit `public EVM init/finalize entrypoints and Wormhole extensions` so that `evm/src/omni-bridge/contracts/OmniBridge.sol recipient/message handling` mutates finalization state before all signature or proof checks implied by serializes recipient and optional strings into signed payloads and Wormhole messages that other chains later parse as `OmniAddress` or application messages are complete, violating `string encoding must not let empty, overlong, or non-canonical forms change who gets paid or which message downstream chains execute`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol recipient/message handling`
- Entrypoint: `public EVM init/finalize entrypoints and Wormhole extensions`
- Attacker controls: recipient string, message bytes, empty versus non-empty optional encoding, and fee-recipient string
- Exploit idea: Look for `completed`, `finalised`, or bitmap writes that happen before every branch-specific validation step and external effect.
- Invariant to test: string encoding must not let empty, overlong, or non-canonical forms change who gets paid or which message downstream chains execute
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force later validation or delivery to fail after replay state was consumed and assert that the transfer cannot be stranded or reopened inconsistently.
