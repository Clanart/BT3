# Q3835: EVM init/finalize optional message semantics delivery callback leaves inconsistent state

## Question
Can an unprivileged attacker trigger a token-delivery callback from `public `initTransfer`, `initTransfer1155`, and `finTransfer`` that causes `evm/src/omni-bridge/contracts/OmniBridge.sol optional `message` handling` to keep or remove settlement state inconsistently with delivered value because of uses message presence to choose between plain mint and message-based mint on bridge tokens and to include or omit Borsh-encoded message bytes in signatures, violating `optional message semantics must not let an attacker switch the economic branch after the source event is fixed, thereby changing whether callbacks can refund or consume tokens`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol optional `message` handling`
- Entrypoint: `public `initTransfer`, `initTransfer1155`, and `finTransfer``
- Attacker controls: empty versus non-empty message bytes and recipient contracts that react differently to message-carrying mints
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records.
- Invariant to test: optional message semantics must not let an attacker switch the economic branch after the source event is fixed, thereby changing whether callbacks can refund or consume tokens
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund.
