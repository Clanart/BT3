# Q3503: EVM ENearProxy burn burn debits the wrong logical account through cross-module drift

## Question
Can an unprivileged attacker use `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows` with control over token address and amount and desynchronize `evm/src/eNear/contracts/ENearProxy.sol::burn` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `burn debits the wrong logical account` attack class because calls `eNear.transferToNear` after asserting the token address is the configured eNEAR token, violating `legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::burn`
- Entrypoint: `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows`
- Attacker controls: token address and amount
- Exploit idea: Target burns keyed to predecessor account, owner, or controller context rather than an explicit subject. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Manipulate caller/proxy layouts and assert that the debited balance always belongs to the asset owner represented in the bridge event. Also assert cross-module consistency between `evm/src/eNear/contracts/ENearProxy.sol::burn` and the adjacent mint, burn, or custody accounting after every branch.
