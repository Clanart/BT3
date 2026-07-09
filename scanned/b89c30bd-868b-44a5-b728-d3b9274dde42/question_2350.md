# Q2350: EVM ENearProxy burn legacy withdrawal shortcut aliases a normal transfer through cross-module drift

## Question
Can an unprivileged attacker use `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows` with control over token address and amount and desynchronize `evm/src/eNear/contracts/ENearProxy.sol::burn` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `legacy withdrawal shortcut aliases a normal transfer` attack class because calls `eNear.transferToNear` after asserting the token address is the configured eNEAR token, violating `legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::burn`
- Entrypoint: `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows`
- Attacker controls: token address and amount
- Exploit idea: Target memo-based or self-transfer-based legacy shortcuts in token wrappers. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Exercise equivalent economic transfers with and without legacy markers and assert that only the intended path creates a bridge event. Also assert cross-module consistency between `evm/src/eNear/contracts/ENearProxy.sol::burn` and the adjacent mint, burn, or custody accounting after every branch.
