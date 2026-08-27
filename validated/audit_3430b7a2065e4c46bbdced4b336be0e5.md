### Title
`incentiveDeposit` mode-2 doubling is applied after the MGP balance cap, letting a single oversized deposit drain the entire incentive pool - ([File: wombat/ArbWomUp3.sol])

### Summary
In `wombat/ArbWomUp3.sol`, `getRewardAmount` already clamps the computed reward to the contract's current MGP balance before returning it, but `incentiveDeposit` then multiplies that already-capped value by 2 when `_mode == 2`, defeating the clamp. By sizing `_amount` so the tier-walk reward (uncapped) lands at exactly half the remaining MGP balance, an unprivileged caller can, in a single transaction, have `rewardToSend` equal the entire remaining MGP balance and lock it all to themselves via `vlMGP.lockFor`.

### Finding Description
`getRewardAmount` computes a tier-based reward and caps it: ` [1](#0-0) ` returns `mgpReward > mgpleft ? mgpleft : mgpReward`, i.e. it can never report more than the contract's live MGP balance.

However, `incentiveDeposit` applies the "mode 2" bonus **after** this cap has already been applied: [2](#0-1) 

Because `rewardToSend` is doubled post-cap, the balance ceiling enforced inside `getRewardAmount` is only a ceiling on the *pre-doubling* value, not on what is actually approved and locked via `vlMGP.lockFor`. An attacker who deposits WOM/mWOM via `_mode == 2` can pick `_amount` (and their pre-existing `mWomSV` locked balance, since the tier walk uses `_amountToConvert + mWomSV.getUserTotalLocked(_account)`) such that the uncapped tier reward equals exactly half of the current MGP balance held by the contract. `getRewardAmount` then returns that half-balance figure unclamped (since it is below `mgpleft`), and the subsequent `* 2` in `incentiveDeposit` produces a `rewardToSend` equal to the contract's full MGP balance, which `vlMGP.lockFor` will successfully transfer in one call — capturing 100% of the pool intended to reward the broader depositor base.

Regarding the specific `bracketRewarded` / `calDoubledCounted` invariant named in the question: `bracketRewarded` is declared but is dead state — it is explicitly annotated "not in use" and is never written or read anywhere in the contract [3](#0-2) . There is no code path that reconciles it with `calDoubledCounted`, so that precise invariant cannot be programmatically tested/broken as described; the real, exploitable defect is the ordering of the balance cap versus the doubling multiplier in `incentiveDeposit`/`getRewardAmount`.

### Impact Explanation
A single unprivileged caller can drain the entire remaining MGP incentive balance held by `ArbWomUp3` in one transaction using only WOM tokens they hold, by choosing `_mode == 2` and sizing the deposit/pre-locked `mWomSV` balance appropriately. This is a direct loss of the protocol's incentive reserve (locked to the attacker via `vlMGP.lockFor`) and prevents all other legitimate participants from receiving their proportional share, matching "theft of unclaimed yield" / direct fund loss from the incentive pot.

### Likelihood Explanation
Exploitation requires only: (1) enough WOM tokens (or a flash-swap/market purchase) to reach the target tier deposit size, (2) knowledge of the current MGP balance of the contract (`IERC20(mgp).balanceOf(address(this))`, publicly readable), and (3) calling `incentiveDeposit` once with `_mode == 2`. No special role is needed, and the attack is fully computable off-chain beforehand (view functions `getRewardAmount`/`calDoubledCounted` are public), making it deterministic and repeatable against any funded deployment of this contract.

### Recommendation
Apply the mode-2 bonus multiplier *before* the balance cap, i.e. move the `* 2` (or the correct 1.5x per the code comment, since the comment says "50% more bonus" but the code does `* 2`) inside `getRewardAmount` prior to the `mgpReward > mgpleft` check, so the final `rewardToSend` returned to `incentiveDeposit` is capped by the actual MGP balance after bonus application. Additionally consider adding a per-transaction/per-account cap independent of total pool balance, and fix the comment/code mismatch between "50% more bonus" and the `* 2` doubling.

### Proof of Concept
Hardhat test plan:
1. Deploy `ArbWomUp3`, `mWomSV`, `vlMGP`, `mWom`, mock `smartWomConvert`, and MGP/WOM ERC20 mocks; call `__arbWomUp_init` and `setMultiplier` with a simple tier table (e.g., tier[1] = 1000e18 with multiplier that yields a computable reward).
2. Fund the `ArbWomUp3` contract with a known, finite `mgp` balance `B` (representing the incentive pot).
3. As an attacker EOA holding only market-bought WOM, compute off-chain (mirroring `getRewardAmount`/`calDoubledCounted` logic) the `_amount` and any needed pre-existing `mWomSV` lock such that the uncapped tier reward equals `B/2`.
4. Call `incentiveDeposit(_amount, _convertRatio, false, 2)` once, in a single transaction.
5. Assert: `IERC20(mgp).balanceOf(ArbWomUp3) == 0` after the call (pool fully drained), and `vlMGP` balance of the attacker equals `B`, while `IERC20(mgp).balanceOf(ArbWomUp3)` before the call was `B` — demonstrating 100% of the incentive pot captured by one actor in one transaction, contrary to the intended per-tier, multi-participant distribution.

### Citations

**File:** wombat/ArbWomUp3.sol (L47-47)
```text
    mapping(address => uint) public bracketRewarded;   // not in use
```

**File:** wombat/ArbWomUp3.sol (L88-105)
```text
    function incentiveDeposit(
        uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode // 1 stake, 2 lock
    ) external _checkAmount(_amount) whenNotPaused nonReentrant {
        if (_amount == 0) return;
        
        uint256 rewardToSend = this.getRewardAmount(_amount, msg.sender, _mode == 2);

        // giving out 50% more bonus
        if (_mode == 2)
            rewardToSend = rewardToSend * 2;

        _deposit(msg.sender, _convertRatio, _amount, _mode);

        IERC20(mgp).safeApprove(address(vlMGP), rewardToSend);
        vlMGP.lockFor(rewardToSend, msg.sender);
        // _bullMGP(rewardToSend, _minMGPRec, msg.sender);
        emit VLMGPRewarded(msg.sender, 0, rewardToSend);
    }
```

**File:** wombat/ArbWomUp3.sol (L107-129)
```text
    function getRewardAmount(uint256 _amountToConvert, address _account, bool _lock) external view returns (uint256) {
        uint256 mgpReward = 0;

        if (!_lock) {
            mgpReward = _amountToConvert * rewardMultiplier[getUserTier(_account)] / DENOMINATOR;
        } else {
            uint256 accumulated = _amountToConvert + mWomSV.getUserTotalLocked(_account);
            uint256 rewardAmount = 0;
            uint256 i = 1;

            while (i < rewardTier.length && accumulated > rewardTier[i]) {
                rewardAmount +=
                    (rewardTier[i] - rewardTier[i - 1]) *
                    rewardMultiplier[i - 1];
                i++;
            }
            rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1];
            mgpReward = (rewardAmount / DENOMINATOR) - calDoubledCounted(_account);
        }

        uint256 mgpleft = IERC20(mgp).balanceOf(address(this));
        return mgpReward > mgpleft ? mgpleft : mgpReward;
    }
```
