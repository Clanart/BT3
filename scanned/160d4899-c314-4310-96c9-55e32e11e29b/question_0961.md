# Q961: EVM bridge recipient/message strings fee and principal split divergence via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public EVM init/finalize entrypoints and Wormhole extensions` and then replay or reorder later fee-claim proof submission so that `evm/src/omni-bridge/contracts/OmniBridge.sol recipient/message handling` ends up accepting two inconsistent interpretations of the same economic event specifically around `fee and principal split divergence` under serializes recipient and optional strings into signed payloads and Wormhole messages that other chains later parse as `OmniAddress` or application messages, violating `string encoding must not let empty, overlong, or non-canonical forms change who gets paid or which message downstream chains execute`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol recipient/message handling`
- Entrypoint: `public EVM init/finalize entrypoints and Wormhole extensions`
- Attacker controls: recipient string, message bytes, empty versus non-empty optional encoding, and fee-recipient string
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: string encoding must not let empty, overlong, or non-canonical forms change who gets paid or which message downstream chains execute
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
