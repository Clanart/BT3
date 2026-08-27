### Title
`WombatBribeManager` swaps bribe reward tokens for BNB with a hardcoded zero minimum output, exposing claimed bribe value to sandwich attacks - (File: `wombat/WombatBribeManager.sol`)

### Summary
`WombatBribeManager._swapFeesForBnb()` converts bribe reward tokens into BNB via `IBNBZapper(PancakeZapper).zapInToken()` and hardcodes the `minRec` (minimum-received) parameter to `0`, meaning the swap has no slippage protection at all.

### Finding Description
In `_swapFeesForBnb`, for every non-zero reward token amount collected from bribe pools, the contract approves the `PancakeZapper` and calls `zapInToken` with a literal `0` passed as the minimum output amount: [1](#0-0) 

`zapInToken` in turn performs `ROUTER.swapExactTokensForETH(amount, minRec, path, receiver, block.timestamp)` using this attacker-controllable-effective `0` as the AMM slippage bound: [2](#0-1) 

Unlike the referenced FeeBurner/SwapperRouter report where the `0` minimum arises only when a Chainlink oracle fails to return a price, here the `0` is unconditional and always applied — every invocation of this bribe-fee-to-BNB conversion path executes with zero slippage protection, regardless of oracle availability. This is a stronger (always-on) instance of the same underlying bug class described in the report: "initiates swap without any slippage checks."

The output of the swap (`bnbAmount`) is sent to `msg.sender`, i.e., the unprivileged wallet that triggers the claim/bribe-conversion flow, so the loss from a sandwiched swap is borne directly by that ordinary caller.

### Impact Explanation
Because there is no minimum-output check, a searcher/MEV actor can front-run and back-run the swap transaction (classic sandwich attack) on the underlying PancakeSwap pool, extracting value from the reward-token-to-BNB conversion. The unprivileged wallet claiming/converting bribe rewards receives materially less BNB than the fair-market amount, resulting in direct loss of unclaimed yield/reward value for the end user — this matches the "theft ... of unclaimed yield" impact category.

### Likelihood Explanation
Likelihood is high: the zero-slippage swap is not an edge case (unlike an oracle outage) but the default and only behavior of this code path every time bribe reward tokens are converted to BNB. Any bribe reward token with meaningful on-chain liquidity depth limitations (thin PancakeSwap pools) is straightforward to sandwich profitably by any MEV searcher monitoring the mempool.

### Recommendation
Compute an expected output (e.g., via `ROUTER.getAmountsOut`) off-chain or on-chain and pass a reasonable `minRec` (e.g., 95-99% of expected) into `_swapFeesForBnb`/`zapInToken`, or allow the caller to supply and enforce a non-zero `minRec` parameter that is validated against a spot/TWAP quote before executing the swap.

### Proof of Concept
1. Bribe reward tokens accumulate in `WombatBribeManager` from various bribe pools.
2. A user calls the (bribe claim/cast) flow that internally invokes `_swapFeesForBnb(rewardTokens, feeAmounts)`.
3. For each non-zero reward token amount, `IBNBZapper(PancakeZapper).zapInToken(token, amount, 0, msg.sender)` is called — note the hardcoded `0` for `minRec`.
4. `BNBZapper._swapTokenForBNB` executes `ROUTER.swapExactTokensForETH(amount, 0, path, receiver, block.timestamp)` with zero slippage protection.
5. A searcher observes the pending transaction, front-runs it by buying the reward token (pushing price up) and back-runs by selling after the victim's swap executes, capturing the price impact that would otherwise go to the user claiming their bribe rewards. [1](#0-0) [3](#0-2)

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

**File:** rewards/BNBZapper.sol (L56-100)
```text
    function zapInToken(
        address fromToken,
        uint256 amount,
        uint256 minRec,
        address receiver
    ) external nonReentrant returns (uint256 bnbAmount) {
        if (amount > 0) {
            IERC20(fromToken).safeTransferFrom(msg.sender, address(this), amount);
            IERC20(fromToken).safeApprove(address(ROUTER), amount);
            bnbAmount = _swapTokenForBNB(fromToken, amount, minRec, receiver);
        }
    }

    /* ============ Internal Functions ============ */
    function _findRouteToBnb(address token) private view returns (address[] memory) {
        address[] memory path;
        if (routePairAddresses[token] != address(0)) {
            path = new address[](3);
            path[0] = token;
            path[1] = routePairAddresses[token];
            path[2] = WBNB;
        } else {
            path = new address[](2);
            path[0] = token;
            path[1] = WBNB;
        }
        return path;
    }

    function _swapTokenForBNB (
        address token,
        uint256 amount,
        uint256 minRec,
        address receiver
    ) private returns (uint256) {
        address[] memory path = _findRouteToBnb(token);
        uint256[] memory amounts = ROUTER.swapExactTokensForETH(
            amount,
            minRec,
            path,
            receiver,
            block.timestamp
        );
        return amounts[amounts.length - 1];
    }
```
