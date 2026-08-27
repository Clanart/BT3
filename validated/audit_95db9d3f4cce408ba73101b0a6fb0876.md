### Title
Missing reentrancy guard on `WombatStaking.vote()` loop over bribe reward tokens allows reentrancy during multi-token payout - ([File: wombat/WombatStaking.sol])

### Summary
`WombatStaking.vote()` iterates over an array of bribe reward tokens per pool and performs `safeTransfer`, `safeApprove`, and `queueNewRewards` external calls to each token/rewarder inside a nested loop, exactly the pattern flagged in the original `createBasket` finding (looping over "various tokens ... that may contain callback hooks"), yet the function carries no `nonReentrant` modifier.

### Finding Description
`vote()` is guarded only by `if(msg.sender != bribeManager) revert OnlyBribeMamager();` [1](#0-0)  — i.e. it is callable by the `WombatBribeManager` contract on behalf of an ordinary voting wallet, not by a privileged EOA. Inside, for every pool voted on, it fetches the pool's bribe reward token list from `IWombatBribe(bribesContract).rewardTokens()` and, for each non‑zero reward amount, performs a sequence of external token operations — `safeTransfer` to `bribeFeeCollector`, `safeTransfer` to `bribeManager` (caller fee), `safeApprove`, and `IBaseRewardPool(...).queueNewRewards(...)` — all inside a double `for` loop with no reentrancy protection: [2](#0-1) 

Because the reward token addresses are sourced from bribe contracts tied to individual pools (bribe depositors control which tokens are used as bribe rewards for a pool), an attacker who bribes a pool with a malicious ERC777/callback-enabled token can trigger arbitrary reentrant code execution during the `safeTransfer` calls in this loop, before the loop (and the outer `WombatBribeManager` voting state) finishes updating.

### Impact Explanation
A reentrant call back into `vote()` (or other unprotected/partially-protected state-changing functions reachable from `WombatStaking`) during the token transfer loop could re-trigger fee transfers or reward queuing against stale/unflushed bribe balances, allowing repeated draining of bribe reward tokens meant for other voters/pools, or manipulation of the reward distribution ordering across pools mid-vote. This can result in theft of bribe/reward funds belonging to other users and/or corruption of the vote-triggered reward distribution, which are impacts within the accepted categories (theft of unclaimed yield, insolvency).

### Likelihood Explanation
Likelihood is moderate: it requires an attacker to place a malicious callback token as a bribe reward for a pool (an action available to unprivileged wallets that fund bribes) and then wait for/trigger a vote cycle. This mirrors the `createBasket` scenario where the loop iterates over externally-influenced, arbitrary ERC20-like tokens with no reentrancy guard.

### Recommendation
Add a `nonReentrant` modifier (via `ReentrancyGuard`/`ReentrancyGuardUpgradeable`) to `WombatStaking.vote()`, consistent with the reentrancy protections already applied to `MasterMagpie.deposit/withdraw` and several `BaseRewardPool*`/`vlMGPBaseRewarder`/`mWOMSVBaseRewarder` reward-claim functions elsewhere in the codebase (e.g. `getRewards` on `vlMGPBaseRewarder.sol`/`mWOMSVBaseRewarder.sol` at lines 249-261 already use `nonReentrant`).

### Proof of Concept
1. Attacker deposits a bribe for pool `P` using a malicious token `M` that implements a transfer hook (e.g., ERC777 `tokensToSend`), registering `M` as one of `IWombatBribe(bribeOf(P)).rewardTokens()`.
2. A vote cycle occurs; `WombatBribeManager` calls `WombatStaking.vote(...)` including pool `P`.
3. Inside the loop at [3](#0-2) , `IERC20(rewardTokens[i][j]).safeTransfer(bribeFeeCollector, protocolFee)` triggers `M`'s hook, which reenters `vote()` (or another externally reachable function) before the outer loop's state (e.g., subsequent `queueNewRewards` for other pools/tokens in the same call) has completed.
4. The reentrant call can process/queue the same or overlapping bribe balances again, double counting fees/rewards or diverting funds intended for other pools/voters.

Note: full confirmation of whether `WombatBribeManager` permits arbitrary/unprivileged bribe-token registration was not directly inspected in this pass; this should be verified against `WombatBribeManager.sol`'s bribe-deposit function before treating this as fully confirmed.

### Citations

**File:** wombat/WombatStaking.sol (L363-371)
```text
    function vote(
        address[] calldata _lpVote,
        int256[] calldata _deltas,
        address[] calldata _rewarders,
        address caller
    ) external returns (IERC20[][] memory rewardTokens, uint256[][] memory callerFeeAmounts) {
        if(msg.sender != bribeManager)
            revert OnlyBribeMamager();
            
```

**File:** wombat/WombatStaking.sol (L378-418)
```text
        for (uint256 i; i < rewardAmounts.length; i++) {

            address bribesContract = address(voter.infos(_lpVote[i]).bribe);

            if (bribesContract != address(0)) {
                rewardTokens[i] = IWombatBribe(bribesContract).rewardTokens();
                callerFeeAmounts[i] = new uint256[](rewardAmounts[i].length);

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
            }
        }
    }
```
