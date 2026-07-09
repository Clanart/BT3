# Q2125: EVM init/finalize optional message semantics stored state versus signed bytes mismatch

## Question
Can an unprivileged attacker use `public `initTransfer`, `initTransfer1155`, and `finTransfer`` so that `evm/src/omni-bridge/contracts/OmniBridge.sol optional `message` handling` stores one economic transfer but signs or publishes different bytes because of uses message presence to choose between plain mint and message-based mint on bridge tokens and to include or omit Borsh-encoded message bytes in signatures, violating `optional message semantics must not let an attacker switch the economic branch after the source event is fixed, thereby changing whether callbacks can refund or consume tokens`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol optional `message` handling`
- Entrypoint: `public `initTransfer`, `initTransfer1155`, and `finTransfer``
- Attacker controls: empty versus non-empty message bytes and recipient contracts that react differently to message-carrying mints
- Exploit idea: Look for canonical-state versus emitted-bytes drift on optional strings, decimals, origin ids, or fee recipients.
- Invariant to test: optional message semantics must not let an attacker switch the economic branch after the source event is fixed, thereby changing whether callbacks can refund or consume tokens
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Compare persisted transfer records to their signed or published payloads and assert byte-for-byte equivalence of all economically-relevant fields.
