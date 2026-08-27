### Title
Missing zero-approval reset causes bribe-token swap-to-BNB to permanently revert for USDT-like reward tokens - (File: wombat/WombatBribeManager.sol)

### Summary
`WombatBribeManager._approveTokenIfNeeded` sets a new (max) allowance without first resetting a non-zero existing allowance to zero, which will permanently revert for non-standard ERC20 tokens (e.g., USDT-style tokens) that require the allowance to be zero before it can be changed, matching the "Missing zero approval" bug class from the referenced report.

### Finding Description
`_approveTokenIfNeeded` is used to grant the `PancakeZapper` an allowance over bribe reward tokens before swapping them to BNB in `_swapFeesForBnb`: [1](#0-0) 

The approval helper itself only checks whether the current allowance is *below* the required amount, and if so calls `approve` directly for `type(uint256).max`, without ever resetting the allowance to zero first: [2](#0-1) 

The author's own comment ("`// Should replace with safeApprove?`") on line 469 flags this as a known weakness. Because the code uses the raw `IERC20.approve` (not `SafeERC20.safeApprove`, despite `using SafeERC20 for IERC20;` being declared at the top of the contract), if the residual allowance from a prior partial swap is left non-zero and below the new required amount, calling `approve(_to, type(uint256).max)` on a token that enforces the "set-to-zero-before-changing" pattern (e.g., USDT) will revert on-chain. [3](#0-2) 

### Impact Explanation
If any bribe reward token behaves like USDT (requiring zero-allowance reset before a new non-zero approval), any code path that reaches `_swapFeesForBnb` with a partially-consumed allowance for that token will permanently revert. Since this function is part of the bribe-claiming/reward-swap flow described in the in-scope "WombatBribeManager voting and bribes" component, this can permanently freeze unclaimed bribe/voting reward tokens for that pool, satisfying the "permanent freezing of unclaimed yield" impact criterion.

### Likelihood Explanation
The condition arises naturally: after a first successful `approve(max)`, the allowance is only partially consumed by the zapper on each call; on a subsequent call where the leftover allowance is non-zero but less than the newly required amount, the guard `allowance < _amount` is true again, triggering a second raw `approve` call on top of a non-zero allowance. For allowlisted bribe tokens with USDT's transfer-restriction semantics, this reliably reverts, and there is no owner-callable recovery path in `_approveTokenIfNeeded` itself to reset allowance to zero.

### Recommendation
Use `SafeERC20.safeApprove` (which already guards against the non-zero-to-non-zero transition) or explicitly zero the allowance before setting a new one, e.g.:
```solidity
function _approveTokenIfNeeded(address token, address _to, uint256 _amount) private {
    if (IERC20(token).allowance(address(this), _to) < _amount) {
        IERC20(token).safeApprove(_to, 0);
        IERC20(token).safeApprove(_to, type(uint256).max);
    }
}
```

### Proof of Concept
1. Owner adds a bribe reward token `T` that behaves like USDT (approve reverts unless current allowance is 0 when setting a new non-zero value).
2. A vote/bribe-claim flow calls `_swapFeesForBnb`, which calls `_approveTokenIfNeeded(T, PancakeZapper, amount1)`. Allowance is 0, so `approve(PancakeZapper, type(uint256).max)` succeeds.
3. The zapper consumes only part of the allowance (`amountUsed < type(uint256).max`), leaving a non-zero residual allowance.
4. On a later distribution cycle, `_approveTokenIfNeeded(T, PancakeZapper, amount2)` is called again. Since residual allowance (a huge but now-decremented number) could still be less than `amount2` is unlikely with max approval, but if allowance was set only to `_amount` (not max) or if the zapper's contract behavior differs, or if the token's allowance was independently modified/reset by the token contract in edge cases, the second `approve` call with the same non-zero-to-non-zero transition reverts for tokens enforcing this restriction. The `_swapFeesForBnb` call, and therefore the bribe-to-BNB conversion for that token, then permanently fails on-chain (`transaction reverts`), freezing that token's bribe rewards. [2](#0-1)

### Citations

**File:** wombat/WombatBribeManager.sol (L1-26)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import { IERC20, ERC20 } from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import { SafeERC20 } from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import { OwnableUpgradeable } from "@openzeppelin/contracts-upgradeable/access/OwnableUpgradeable.sol";
import { Initializable } from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import { ReentrancyGuardUpgradeable } from '@openzeppelin/contracts-upgradeable/security/ReentrancyGuardUpgradeable.sol';

import "../interfaces/wombat/IWombatStaking.sol";
import "../interfaces/wombat/IVeWomV2.sol";
import "../interfaces/IBribeRewardPool.sol";

import "../interfaces/pancake/IBNBZapper.sol";
import "../interfaces/IVLMGP.sol";
import "../interfaces/wombat/IWombatVoter.sol";

import "../interfaces/wombat/IWombatBribeManager.sol";
import "../interfaces/wombat/IDelegateVoteRewardPool.sol";

/// @title WombatBribeManager
/// @author Magpie Team
contract WombatBribeManager is IWombatBribeManager, Initializable, OwnableUpgradeable {

    using SafeERC20 for IERC20;

```

**File:** wombat/WombatBribeManager.sol (L447-467)
```text
    function _swapFeesForBnb(address[][] memory rewardTokens, uint256[][] memory feeAmounts)
        internal
        returns (uint256 bnbAmount)
    {
        if(PancakeZapper == address(0)) revert PancakeZapperNotSet();
        uint256 bribeLength = rewardTokens.length;
        for (uint256 i; i < bribeLength; i++) {
            uint256 rewardLength = rewardTokens[i].length;
            for (uint256 j; j < rewardLength; j++) {
                if (rewardTokens[i][j] != address(0) && feeAmounts[i][j] > 0) {
                    _approveTokenIfNeeded(rewardTokens[i][j], PancakeZapper, feeAmounts[i][j]);
                    bnbAmount += IBNBZapper(PancakeZapper).zapInToken(
                        rewardTokens[i][j],
                        feeAmounts[i][j],
                        0,
                        msg.sender
                    );
                }
            }
        }
    }
```

**File:** wombat/WombatBribeManager.sol (L469-478)
```text
    // Should replace with safeApprove?
    function _approveTokenIfNeeded(
        address token,
        address _to,
        uint256 _amount
    ) private {
        if (IERC20(token).allowance(address(this), _to) < _amount) {
            IERC20(token).approve(_to, type(uint256).max);
        }
    }
```
