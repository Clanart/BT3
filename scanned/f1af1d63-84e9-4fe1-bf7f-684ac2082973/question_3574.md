# Q3574: EVM init/finalize optional message semantics asset-branch confusion on finalization through cross-module drift

## Question
Can an unprivileged attacker use `public `initTransfer`, `initTransfer1155`, and `finTransfer`` with control over empty versus non-empty message bytes and recipient contracts that react differently to message-carrying mints and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol optional `message` handling` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `asset-branch confusion on finalization` attack class because uses message presence to choose between plain mint and message-based mint on bridge tokens and to include or omit Borsh-encoded message bytes in signatures, violating `optional message semantics must not let an attacker switch the economic branch after the source event is fixed, thereby changing whether callbacks can refund or consume tokens`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol optional `message` handling`
- Entrypoint: `public `initTransfer`, `initTransfer1155`, and `finTransfer``
- Attacker controls: empty versus non-empty message bytes and recipient contracts that react differently to message-carrying mints
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: optional message semantics must not let an attacker switch the economic branch after the source event is fixed, thereby changing whether callbacks can refund or consume tokens
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol optional `message` handling` and the adjacent mint, burn, or custody accounting after every branch.
