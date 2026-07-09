# Q329: EVM init/finalize optional message semantics recipient or message ambiguity via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public `initTransfer`, `initTransfer1155`, and `finTransfer`` and then replay or reorder later callback or refund resolution so that `evm/src/omni-bridge/contracts/OmniBridge.sol optional `message` handling` ends up accepting two inconsistent interpretations of the same economic event specifically around `recipient or message ambiguity` under uses message presence to choose between plain mint and message-based mint on bridge tokens and to include or omit Borsh-encoded message bytes in signatures, violating `optional message semantics must not let an attacker switch the economic branch after the source event is fixed, thereby changing whether callbacks can refund or consume tokens`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol optional `message` handling`
- Entrypoint: `public `initTransfer`, `initTransfer1155`, and `finTransfer``
- Attacker controls: empty versus non-empty message bytes and recipient contracts that react differently to message-carrying mints
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: optional message semantics must not let an attacker switch the economic branch after the source event is fixed, thereby changing whether callbacks can refund or consume tokens
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
