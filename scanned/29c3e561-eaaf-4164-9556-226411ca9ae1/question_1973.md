# Q1973: EVM init/finalize optional message semantics native versus wrapped branch switch at boundary values

## Question
Can an unprivileged attacker trigger `public `initTransfer`, `initTransfer1155`, and `finTransfer`` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `evm/src/omni-bridge/contracts/OmniBridge.sol optional `message` handling` violate `optional message semantics must not let an attacker switch the economic branch after the source event is fixed, thereby changing whether callbacks can refund or consume tokens` in the `native versus wrapped branch switch` attack class because uses message presence to choose between plain mint and message-based mint on bridge tokens and to include or omit Borsh-encoded message bytes in signatures becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol optional `message` handling`
- Entrypoint: `public `initTransfer`, `initTransfer1155`, and `finTransfer``
- Attacker controls: empty versus non-empty message bytes and recipient contracts that react differently to message-carrying mints
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: optional message semantics must not let an attacker switch the economic branch after the source event is fixed, thereby changing whether callbacks can refund or consume tokens
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
