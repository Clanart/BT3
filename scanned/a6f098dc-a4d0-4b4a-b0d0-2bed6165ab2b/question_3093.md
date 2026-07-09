# Q3093: EVM ENearProxy burn migration swap leaves old and new claims live at boundary values

## Question
Can an unprivileged attacker trigger `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/eNear/contracts/ENearProxy.sol::burn` violate `legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once` in the `migration swap leaves old and new claims live` attack class because calls `eNear.transferToNear` after asserting the token address is the configured eNEAR token becomes fragile at those edges?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::burn`
- Entrypoint: `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows`
- Attacker controls: token address and amount
- Exploit idea: Target old/new token migration flows that combine bridge burning and replacement minting. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Track both token supplies and pending transfer state and assert that migrating one unit cannot leave two redeemable claims. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
