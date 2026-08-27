### Title
Non-zero-to-non-zero `approve()` in `_approveTokenIfNeeded` permanently freezes bribe-fee swaps for restrictive ERC20 tokens (e.g. USDT) - (`wombat/WombatBribeManager.sol`)

### Summary
`WombatBribeManager._approveTokenIfNeeded` increases the allowance to `type(uint256).max` only when the current allowance is insufficient, but never resets the allowance to zero first. For ERC20 tokens that require the allowance to go from zero before it can be set to a new non-zero value (e.g. USDT), a second increase call after the allowance has been partially spent down (but not to zero) will always revert, permanently bricking fee-swapping/distribution for that bribe reward token.

### Finding Description
`_approveTokenIfNeeded` is used to grant `PancakeZapper` an allowance over arbitrary bribe reward tokens before swapping them for BNB: [1](#0-0) 

```solidity
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

This function is invoked from `_swapFeesForBnb` for every reward token collected as a bribe, iterating over `rewardTokens[i][j]` amounts: [2](#0-1) 

The flow is: first call, allowance is `0`, so it goes `0 -> max`, which works even for USDT. As `PancakeZapper` spends down that allowance over successive swaps, the remaining allowance can fall to a non-zero value that is still less than the next `_amount` needed. At that point `_approveTokenIfNeeded` calls `approve(_to, type(uint256).max)` again while the current allowance is non-zero. Tokens like USDT require the allowance to be reset to `0` before it can be changed to a new non-zero value, so this second `approve` call reverts unconditionally going forward. Because reward tokens for bribes are added by pool/bribe configuration and are not restricted to a single "safe" asset (unlike `tigAsset`-only assumption cited in the referenced report's resolution), any pool or briber can introduce a USDT-like reward token, permanently freezing the swap path for that token.

### Impact Explanation
Once the allowance for a restrictive token like USDT drops to a non-zero value below the next required swap amount, all subsequent `_swapFeesForBnb` calls involving that token revert. Since this function is part of the bribe fee-to-BNB conversion pipeline, this permanently freezes any bribe rewards denominated in that token — they can never be swapped/distributed to voters, resulting in a permanent freeze of unclaimed yield for the affected bribe reward token.

### Likelihood Explanation
This requires no privileged action: any reward/bribe token configured for a pool (which is a realistic operational state, not an admin misconfiguration) can be a restrictive ERC20 like USDT. The revert condition is triggered by ordinary, expected usage patterns (allowance being partially consumed across multiple swap cycles), not an edge case requiring attacker intervention.

### Recommendation
Reset the allowance to zero before increasing it, e.g.:
```solidity
function _approveTokenIfNeeded(address token, address _to, uint256 _amount) private {
    if (IERC20(token).allowance(address(this), _to) < _amount) {
        IERC20(token).approve(_to, 0);
        IERC20(token).approve(_to, type(uint256).max);
    }
}
```
Alternatively use `safeIncreaseAllowance`/`forceApprove` from OpenZeppelin's `SafeERC20`, which the contract already imports (`using SafeERC20 for IERC20;`).

### Proof of Concept
1. Owner (or pool config) adds a bribe reward token that behaves like USDT (requires zero-allowance reset).
2. `_swapFeesForBnb` is triggered as part of bribe processing, calling `_approveTokenIfNeeded(token, PancakeZapper, amount1)`; allowance goes `0 -> max` successfully.
3. `PancakeZapper.zapInToken` consumes part of the allowance, leaving it at some non-zero value `X`.
4. On the next bribe cycle, `_approveTokenIfNeeded(token, PancakeZapper, amount2)` is called where `amount2 > X`; since `allowance (X) < amount2`, it attempts `approve(PancakeZapper, type(uint256).max)` while allowance is non-zero — this reverts for USDT-like tokens.
5. All future swaps for this token permanently revert, freezing the associated bribe rewards. [1](#0-0)

### Citations

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
