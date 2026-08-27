### Title
Balance-diff based reward/LP accounting in `WombatStaking` can underflow and permanently brick deposits, withdrawals and harvests - (File: `wombat/WombatStaking.sol`)

### Summary
`WombatStaking.sol` computes LP/WOM/bonus-token amounts received from external calls (Wombat pools, MasterWombat) by taking a "balance after" minus "balance before" snapshot, exactly the pattern flagged in the external report as unsafe when a tracked balance is assumed to never decrease. Because Solidity 0.8 reverts on underflow, any scenario where the external balance ends up lower than the snapshot taken just before the external call causes every subsequent call to `deposit`, `depositLP`, `withdraw`, and `harvest` for that pool to revert permanently, freezing user LP custody and unclaimed rewards.

### Finding Description
`deposit()` and `depositLP()` derive the LP amount actually received from Wombat by diffing balances: [1](#0-0) 

`withdraw()` similarly diffs `depositToken` balance before/after the external `IWombatPool.withdraw` call and transfers the delta to the user: [2](#0-1) 

The core reward-forwarding routine `_toMasterWomAndSendReward`, invoked from every `deposit`, `depositLP`, `withdraw`, and `harvest` call, snapshots WOM and bonus-token balances, performs the external stake/withdraw call (which itself triggers a Wombat harvest), and then computes the reward amount as `balanceAfter - balanceBefore`: [3](#0-2) 

All of these subtractions implicitly assume the tracked ERC20 balance of `address(this)` cannot be lower after the external call than it was before it — the exact assumption the external report identifies as unsafe for rebasing/upgradeable/hackable tokens. Bonus tokens are admin-added via `addBonusRewardForAsset`, but the underflow is triggered purely by an ordinary user's `deposit`/`withdraw`/`harvest` transaction once such a token (or the LP/deposit token itself) experiences any balance decrease between the two snapshots — for example a rebasing or fee-charging bonus token, a MasterWombat/pool implementation quirk that pulls tokens instead of only pushing them, or a token upgrade/exploit that reduces the contract's balance. There is no try/catch, explicit check, or floor at zero anywhere in these diff computations, so Solidity 0.8's built-in overflow/underflow protection reverts the entire transaction.

### Impact Explanation
Because `_toMasterWomAndSendReward` is invoked unconditionally on `deposit`, `depositLP`, `withdraw`, and `harvest` for the affected pool, a single balance decrease in any tracked bonus token (or in the LP/deposit token diff used by `deposit`/`withdraw`) makes the underflow reproducible on every future call. This permanently freezes: (1) all future deposits into that pool, (2) all withdrawals of already-deposited LP by every user of that pool (their receipt tokens remain but the underlying LP can no longer be unstaked/withdrawn through `WombatStaking`), and (3) accrual/distribution of pending WOM and bonus rewards. This matches the "permanent freezing of funds" / "24-hour-plus freeze" criteria, since there is no admin recovery path shown for un-bricking the diff once the underflow condition is met (short of removing the bonus token and losing the harvested amount already implicitly desynced).

### Likelihood Explanation
The trigger condition — a monitored balance decreasing between two back-to-back external calls in the same transaction — is entirely plausible with real-world bonus/reward tokens (fee-on-transfer, rebasing-down, paused/upgraded tokens), and once it occurs it deterministically reverts every subsequent call to the four affected entry points, not just a one-off transaction. The path is reachable purely by ordinary users calling `deposit`/`withdraw`/`harvest` (routed through the pool helper contracts), with no privileged action required to trigger the freeze once the precondition token behavior exists.

### Recommendation
Replace balance-diff arithmetic with saturating computations (e.g., `after > before ? after - before : 0`) or explicit checks with clear reverts distinguishing an "unexpected balance decrease" from a legitimate zero-reward case, so a decreasing balance cannot underflow and permanently disable `deposit`, `depositLP`, `withdraw`, and `harvest`. Consider isolating bonus-token reward computation so failures there cannot block core LP deposit/withdraw flows (e.g., wrap bonus-token accounting in a try/catch and skip the affected token rather than reverting the whole transaction).

### Proof of Concept
1. Owner adds a bonus reward token via `addBonusRewardForAsset(lpToken, bonusToken)` where `bonusToken` is (or becomes) a token whose balance can decrease unexpectedly (rebasing-down, fee-on-transfer, or later-compromised token) — this is a normal, expected admin configuration, not a malicious action.
2. `_rewardBeforeBalances` snapshots `bonusToken.balanceOf(WombatStaking)` at line 703 before any user's `deposit`/`withdraw`/`harvest` call. [4](#0-3) 
3. During the external interaction (e.g., the token's rebase mechanism fires, or its own transfer hooks reduce holder balances) the bonus token balance of `WombatStaking` decreases below `beforeBalances[i]`.
4. Any ordinary user calling `deposit`, `depositLP`, `withdraw`, or `harvest` on that pool now hits `bonusBalanceDiff = IERC20(bonusTokens[i]).balanceOf(address(this)) - beforeBalances[i]` at line 688, which underflows and reverts under Solidity 0.8's checked arithmetic. [5](#0-4) 
5. Because this code path executes unconditionally for every future `deposit`/`withdraw`/`harvest` on the pool, all such calls now permanently revert, locking user LP and unclaimed rewards in `WombatStaking` with no way to withdraw through the intended flow.

### Citations

**File:** wombat/WombatStaking.sol (L255-266)
```text
        uint256 beforeBalance = IERC20(poolInfo.lpAddress).balanceOf(address(this));
        IWombatPool(poolInfo.depositTarget).deposit(
            depositToken,
            _amount,
            _minimumLiquidity,
            address(this),
            block.timestamp,
            false
        );

        uint256 lpReceived = IERC20(poolInfo.lpAddress).balanceOf(address(this)) - beforeBalance;
        _toMasterWomAndSendReward(_lpAddress, lpReceived, true); // triggers harvest from wombat exchange
```

**File:** wombat/WombatStaking.sol (L306-318)
```text
        uint256 beforeWithdraw = IERC20(poolInfo.depositToken).balanceOf(address(this));
        IWombatPool(poolInfo.depositTarget).withdraw(
            poolInfo.depositToken,
            _liquidity,
            _minAmount,
            address(this),
            block.timestamp
        );

        IERC20(poolInfo.depositToken).safeTransfer(
            _sender,
            IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw
        );
```

**File:** wombat/WombatStaking.sol (L671-705)
```text
    function _toMasterWomAndSendReward(address _lpToken, uint256 lpAmount, bool _isStake) internal {
        Pool storage poolInfo = pools[_lpToken];

        address[] memory bonusTokens = assetToBonusRewards[_lpToken];
        uint256 bonusTokensLength = bonusTokens.length;

        uint256 womBeforeBalance = IERC20(wom).balanceOf(address(this));
        uint256[] memory beforeBalances = _rewardBeforeBalances(_lpToken);

        if(_isStake)
            _stakeToWombatMaster(_lpToken, lpAmount); // triggers harvest from wombat exchange
        else
            IMasterWombat(masterWombat).withdraw(poolInfo.pid, lpAmount); // triggers harvest from wombat exchange
        uint256 womRewards = IERC20(wom).balanceOf(address(this)) - womBeforeBalance;
        _sendRewards(_lpToken, wom, poolInfo.rewarder, womRewards);

        for (uint256 i; i < bonusTokensLength; i++) {
            uint256 bonusBalanceDiff = IERC20(bonusTokens[i]).balanceOf(address(this)) - beforeBalances[i];
            if (bonusBalanceDiff > 0) {
                _sendRewards(_lpToken, bonusTokens[i], poolInfo.rewarder, bonusBalanceDiff);
            }
        }

        emit WomHarvested(womRewards);

    }

    function _rewardBeforeBalances(address _lpToken) internal view returns(uint256[] memory beforeBalances) {
        address[] memory bonusTokens = assetToBonusRewards[_lpToken];
        uint256 bonusTokensLength = bonusTokens.length;
        beforeBalances = new uint256[](bonusTokensLength);
        for (uint256 i; i < bonusTokensLength; i++) {
            beforeBalances[i] = IERC20(bonusTokens[i]).balanceOf(address(this));
        }
    }
```
