# Q3771: EVM ENearProxy mint migration swap leaves old and new claims live

## Question
Can an unprivileged attacker route value through `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows` so that `evm/src/eNear/contracts/ENearProxy.sol::mint` burns the old token but still leaves a live claim on the old path while minting the new token, violating `legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::mint`
- Entrypoint: `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows`
- Attacker controls: recipient address, amount, current receipt id, and fake-proof bytes assembled from contract state
- Exploit idea: Target old/new token migration flows that combine bridge burning and replacement minting.
- Invariant to test: legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Track both token supplies and pending transfer state and assert that migrating one unit cannot leave two redeemable claims.
