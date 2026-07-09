# Q2652: EVM ENearProxy burn migration swap leaves old and new claims live

## Question
Can an unprivileged attacker route value through `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows` so that `evm/src/eNear/contracts/ENearProxy.sol::burn` burns the old token but still leaves a live claim on the old path while minting the new token, violating `legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::burn`
- Entrypoint: `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows`
- Attacker controls: token address and amount
- Exploit idea: Target old/new token migration flows that combine bridge burning and replacement minting.
- Invariant to test: legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Track both token supplies and pending transfer state and assert that migrating one unit cannot leave two redeemable claims.
