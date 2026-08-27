[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** wombat/WombatBribeManager.sol (L196-199)
```text
                if (delta > 0) {
                    pool.totalVoteInVlmgp += uint256(delta);
                    userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] += uint256(delta);
                    IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta));
```

**File:** wombat/WombatBribeManager.sol (L256-296)
```text
            uint256 currentVote = getVoteForLp(pool.poolAddress);
            uint256 targetVoteInLMGP = pool.totalVoteInVlmgp;
            uint256 targetVote = 0;

            if (totalVlMgpInVote != 0) {
                targetVote = targetVoteInLMGP * totalVotes() / totalVlMgpInVote;
            }

            if (targetVote >= currentVote) {
                votes[i] = int256(targetVote - currentVote);
            } else {
                votes[i] = int256(targetVote) - int256(currentVote);
            }
        }

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

        // send rewards to the delegate pool
        if (delegatedPool != address(0)) IDelegateVoteRewardPool(delegatedPool).harvestAll();

        emit VoteCasted(msg.sender, lastCastTime);
    }
```

**File:** rewards/BribeRewardPool.sol (L57-67)
```text
    function stakeFor(address _for, uint256 _amount)
        external
        virtual
        onlyOperator
        updateRewards(_for, rewardTokens)
    {
        totalSupply = totalSupply + _amount;
        _balances[_for] = _balances[_for] + _amount;

        emit Staked(_for, _amount);
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L301-312)
```text
        if (totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**stakingTokenDecimals) /
                totalStaked();
        }
```
