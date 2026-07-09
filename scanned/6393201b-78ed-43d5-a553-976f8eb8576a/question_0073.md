# Q73: EVM ENearProxy burn burn or lock before irreversible state

## Question
Can an unprivileged attacker use `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows` to force `evm/src/eNear/contracts/ENearProxy.sol::burn` to burn or lock assets before the transfer record becomes safely irreversible, and then recover or redirect the bridge flow via calls `eNear.transferToNear` after asserting the token address is the configured eNEAR token, violating `legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::burn`
- Entrypoint: `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows`
- Attacker controls: token address and amount
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed.
- Invariant to test: legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped.
