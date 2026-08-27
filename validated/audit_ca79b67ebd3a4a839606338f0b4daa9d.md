### Title
Front-running `castVotes`/`castVotesAndClaimBribes` with free `harvestSinglePool` denies the harvest caller fee - ([File: wombat/WombatBribeManager.sol])

### Summary
`WombatBribeManager.harvestSinglePool` is a public, unauthenticated function that forwards a zero-vote to `WombatStaking.vote(..., address(0))`, which harvests all pending Wombat bribes for the given LPs without paying any caller fee (fee is only paid when `caller != address(0)`). Any unprivileged address can call it immediately before a user's `castVotes`/`castVotesAndClaimBribes` transaction to drain the pending bribes at zero cost, causing the legitimate caller's subsequent `castVotes()` call to compute `feeAmounts = 0` for those pools.

### Finding Description
`castVotes()` computes the caller's fee from the reward amounts returned by `wombatStaking.vote()`: [1](#0-0) 
which internally derives `callerFeeAmount` proportionally to `rewardAmount` returned from the external `voter.vote()` call: [2](#0-1) 

`harvestSinglePool()` is public with no access control and passes `votes[i] = 0` and `caller = address(0)` to `wombatStaking.vote()`: [3](#0-2) 

Since `bribeCallerFee` is only applied `if (caller != address(0) && bribeCallerFee != 0)` [4](#0-3) , calling `harvestSinglePool` harvests and flushes all pending bribes for the target pools into the rewarder (net of protocol fee) while paying zero caller fee. Because this call resets the pending-bribe accrual on the underlying Wombat voter, an attacker can front-run any user's `castVotesAndClaimBribes(lps, swapForBnb)`/`castVotes(swapForBnb)` by calling `harvestSinglePool(lps)` one block earlier with the same LP list. The victim's subsequent `castVotes()` call then computes `feeAmounts = 0` for those pools (bribes already harvested), so `finalFeeAmounts` returned/forwarded to the caller via `_forwardRewards` is zero, permanently denying the fee that was meant to compensate them for the gas-intensive cast.

No modifier, `nonReentrant`, or access control on `harvestSinglePool` prevents this, and it requires zero vlMGP, zero stake, and zero cost beyond gas.

### Impact Explanation
This breaks the intended caller-fee incentive: any unprivileged actor can permanently and repeatably deny the harvest/cast caller fee to any user calling `castVotes`/`castVotesAndClaimBribes`, at negligible gas cost, with no capital requirement. The diverted amount (unclaimed yield owed to the fee-earning caller) is redirected to the pool rewarder instead of the intended caller, matching the "theft or permanent freezing of unclaimed yield" impact class — the specific caller who would have earned the fee permanently loses it.

### Likelihood Explanation
Fully feasible and repeatable: `harvestSinglePool` is a public function with no restrictions, callable by any EOA holding zero vlMGP and zero capital other than gas. It can be trivially front-run (mempool-observed) before any `castVotes`/`castVotesAndClaimBribes` transaction, and can be executed every time a cast is expected, permanently suppressing the fee incentive.

### Recommendation
Restrict `harvestSinglePool` (e.g., to `onlyOwner`/`allowedOperator`/`rewardManager`) or remove the zero-cost harvest griefing vector — e.g., by still paying a caller fee (or a portion) even when harvesting via `harvestSinglePool`, or by disallowing `harvestSinglePool` from being called within a cooldown window before/around `castVotes`, so an unprivileged address cannot flush bribes without triggering the fee mechanism.

### Proof of Concept
Foundry test plan:
1. Deploy/fork the Wombat + Magpie bribe stack with `WombatBribeManager`, `WombatStaking`, and a mock `voter`/bribe contract that accrues bribes over time and returns non-zero `rewardAmounts` from `vote()`.
2. Setup pool(s) with active bribes and non-zero `bribeCallerFee`.
3. Scenario A (baseline): advance time so bribes accrue, then have `userA` (attacker, 0 vlMGP) call nothing; `userB` calls `castVotesAndClaimBribes(lps, false)`; assert `userB` receives `finalFeeAmounts > 0` (via balance delta of reward token).
4. Scenario B (attack): advance time identically, then have `userA` call `harvestSinglePool(lps)` in block N; in block N+1 `userB` calls `castVotesAndClaimBribes(lps, false)`; assert `userB`'s reward-token balance delta (fee) is `0`, while the rewarder pool received the full amount minus protocol fee.
5. Compare fee paid to `userB` in Scenario A vs Scenario B to demonstrate the fee is fully denied due to the front-run `harvestSinglePool` call, at zero cost/benefit to `userA`.

### Citations

**File:** wombat/WombatBribeManager.sol (L271-290)
```text
        (address[][] memory rewardTokens, uint256[][] memory feeAmounts) = wombatStaking.vote(
            _pools,
            votes,
            rewarders,
            msg.sender
        );

        // comment outs for now since chainlink fails sometimes
        // if (swapForBnb) {
        //     finalFeeAmounts = new uint256[][](1);
        //     finalFeeAmounts[0] = new uint256[](1);
        //     finalFeeAmounts[0][0] = _swapFeesForBnb(rewardTokens, feeAmounts);
        //     finalRewardTokens = new address[][](1);
        //     finalRewardTokens[0] = new address[](1);
        //     finalRewardTokens[0][0] = address(0);
        // } else {
            _forwardRewards(rewardTokens, feeAmounts);
            finalRewardTokens = rewardTokens;
            finalFeeAmounts = feeAmounts;
        // }
```

**File:** wombat/WombatBribeManager.sol (L300-311)
```text
    function harvestSinglePool(address[] calldata _lps) public {
        uint256 length = _lps.length;
        int256[] memory votes = new int256[](length);
        address[] memory rewarders = new address[](length);
        for (uint256 i; i < length; i++) {
            address lp = _lps[i];
            Pool storage pool = poolInfos[lp];
            rewarders[i] = pool.rewarder;
            votes[i] = 0;
        }
        wombatStaking.vote(_lps, votes, rewarders, address(0));
    }
```

**File:** wombat/WombatStaking.sol (L386-415)
```text
                for (uint256 j; j < rewardAmounts[i].length; j++) {
                    uint256 rewardAmount = rewardAmounts[i][j];
                    uint256 callerFeeAmount = 0;

                    if (rewardAmount > 0) {
                        // if reward token is bnb, wrap it first
                        if (address(rewardTokens[i][j]) == address(0)) {
                            Address.sendValue(payable(wbnb), rewardAmount);
                            rewardTokens[i][j] = IERC20(wbnb);
                        }

                        uint256 protocolFee = (rewardAmount * bribeProtocolFee) / DENOMINATOR;

                        if (protocolFee > 0) {
                            IERC20(rewardTokens[i][j]).safeTransfer(bribeFeeCollector, protocolFee);
                        }

                        if (caller != address(0) && bribeCallerFee != 0) {
                            callerFeeAmount = (rewardAmount * bribeCallerFee) / DENOMINATOR;
                            IERC20(rewardTokens[i][j]).safeTransfer(bribeManager, callerFeeAmount);
                        }

                        rewardAmount -= protocolFee;
                        rewardAmount -= callerFeeAmount;
                        IERC20(rewardTokens[i][j]).safeApprove(_rewarders[i], rewardAmount);
                        IBaseRewardPool(_rewarders[i]).queueNewRewards(rewardAmount, address(rewardTokens[i][j]));
                    }

                    callerFeeAmounts[i][j] = callerFeeAmount;
                }
```
