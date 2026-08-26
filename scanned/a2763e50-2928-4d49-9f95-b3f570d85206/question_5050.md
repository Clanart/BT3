# Q5050: WombatBribeManager.castVotes - last-block vote sniping before a permissionless cast

## Question
wombat/WombatBribeManager.sol - castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Can an unprivileged attacker controlling the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination, under the attacker passes the same lp address several times in one array, exploit this through `castVotes(bool swapForBnb)` to break the reconciliation between `userTotalVotedInVlmgp[msg.sender]` and `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` and the invariant that an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: last-block vote sniping before a permissionless cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Precondition: the attacker passes the same lp address several times in one array.
- Invariant to test: an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `castVotes(bool swapForBnb)`: constrain the setup so that the attacker passes the same lp address several times in one array, fuzz the attacker inputs (the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination), and assert after every call that an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed.
