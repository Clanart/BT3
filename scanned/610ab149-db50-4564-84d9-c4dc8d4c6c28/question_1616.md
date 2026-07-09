# Q1616: EVM bridge recipient/message strings stored state versus signed bytes mismatch via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public EVM init/finalize entrypoints and Wormhole extensions` and then replay or reorder later fee-claim proof submission so that `evm/src/omni-bridge/contracts/OmniBridge.sol recipient/message handling` ends up accepting two inconsistent interpretations of the same economic event specifically around `stored state versus signed bytes mismatch` under serializes recipient and optional strings into signed payloads and Wormhole messages that other chains later parse as `OmniAddress` or application messages, violating `string encoding must not let empty, overlong, or non-canonical forms change who gets paid or which message downstream chains execute`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol recipient/message handling`
- Entrypoint: `public EVM init/finalize entrypoints and Wormhole extensions`
- Attacker controls: recipient string, message bytes, empty versus non-empty optional encoding, and fee-recipient string
- Exploit idea: Look for canonical-state versus emitted-bytes drift on optional strings, decimals, origin ids, or fee recipients. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: string encoding must not let empty, overlong, or non-canonical forms change who gets paid or which message downstream chains execute
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Compare persisted transfer records to their signed or published payloads and assert byte-for-byte equivalence of all economically-relevant fields. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
