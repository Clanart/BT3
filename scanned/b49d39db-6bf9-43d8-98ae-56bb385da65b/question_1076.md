# Q1076: EVM ENearProxy burn native versus wrapped branch switch through cross-module drift

## Question
Can an unprivileged attacker use `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows` with control over token address and amount and desynchronize `evm/src/eNear/contracts/ENearProxy.sol::burn` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `native versus wrapped branch switch` attack class because calls `eNear.transferToNear` after asserting the token address is the configured eNEAR token, violating `legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::burn`
- Entrypoint: `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows`
- Attacker controls: token address and amount
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Also assert cross-module consistency between `evm/src/eNear/contracts/ENearProxy.sol::burn` and the adjacent mint, burn, or custody accounting after every branch.
