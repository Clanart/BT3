### Title
WETH-native double counting in `pay()` silently consumes stranded ETH from prior callers — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

The `pay()` helper in `PeripheryPayments` unconditionally consumes any native ETH sitting on the router when settling a WETH-denominated swap, without verifying that the ETH was sent by the current caller. Because `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, and `addLiquidityExactShares` are all `external payable` and never auto-refund excess `msg.value`, a victim who sends more ETH than the swap requires strands the surplus on the router. Any subsequent WETH swap by any caller silently spends that surplus, giving the second caller a free or discounted settlement at the first caller's expense.

### Finding Description

`PeripheryPayments.pay()` contains the following WETH branch:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
``` [1](#0-0) 

The router's entire native ETH balance is consumed in priority over pulling WETH from the registered `payer`. There is no check that `address(this).balance` was contributed by the current transaction's `msg.sender`.

The `receive()` guard only blocks plain ETH pushes from non-WETH addresses:

```solidity
receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
}
``` [2](#0-1) 

It does **not** block `msg.value` arriving through a normal function call. Every public swap and liquidity entry point is `external payable`, so a caller can send any amount of ETH. If they send more than the swap consumes, the surplus stays on the router with no automatic refund.

Neither `exactInputSingle` nor any other swap function refunds excess ETH:

```solidity
function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    ...
    _clearExpectedCallbackPool();
    // no refundETH() call
}
``` [3](#0-2) 

The same applies to `exactInput`, `exactOutputSingle`, `exactOutput`, and both overloads of `addLiquidityExactShares` / `addLiquidityWeighted` in `MetricOmmPoolLiquidityAdder`. [4](#0-3) 

### Impact Explanation

A victim who sends `msg.value = V` but whose swap only needs `A < V` leaves `V − A` ETH on the router. The next caller who executes any WETH swap with `amountIn ≤ V − A` pays **zero WETH** from their own balance; the router's stranded ETH covers the entire settlement. The victim permanently loses `V − A` ETH; the attacker receives the swap output for free. The loss scales with the surplus sent and is bounded only by the victim's wallet balance.

### Likelihood Explanation

- All swap and liquidity entry points are `external payable`, inviting ETH alongside any call.
- Users routinely over-send ETH to avoid reverts when the exact amount is uncertain (e.g., slippage, partial fills).
- The intended recovery path (`refundETH()` inside a `multicall`) is opt-in and easily omitted when calling entry points directly.
- An attacker needs only to watch the router's ETH balance (a single `eth_getBalance` RPC call) and front-run or follow the victim's transaction with a WETH swap of matching size.
- No special privilege, no malicious setup, and no non-standard token is required.

### Recommendation

Track the ETH contributed by the current transaction in transient storage (e.g., store `msg.value` at entry and deduct from it inside `pay()`). Only consume native ETH up to the amount the current caller deposited in this transaction. Alternatively, auto-refund any remaining `address(this).balance` at the end of every public entry point, or require callers to always wrap ETH to WETH before calling the router.

### Proof of Concept

```
Block N:
  Victim calls exactInputSingle{value: 2 ETH}(
      pool=WETH/TOKEN1, tokenIn=WETH, amountIn=1 ETH, ...
  )
  → pay(WETH, victim, pool, 1 ETH) fires
  → nativeBalance=2 ETH >= value=1 ETH
  → router deposits 1 ETH → WETH, transfers to pool
  → 1 ETH remains on router
  → victim does NOT call refundETH()

Block N (same or next):
  Attacker calls exactInputSingle{value: 0}(
      pool=WETH/TOKEN1, tokenIn=WETH, amountIn=1 ETH, ...
  )
  → pay(WETH, attacker, pool, 1 ETH) fires
  → nativeBalance=1 ETH >= value=1 ETH
  → router deposits victim's 1 ETH → WETH, transfers to pool
  → attacker receives TOKEN1 output, pays 0 WETH from own balance

Result: victim loses 1 ETH; attacker receives full swap output for free.
``` [5](#0-4) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L69-88)
```text
  function pay(address token, address payer, address recipient, uint256 value) internal {
    // If the payer is contract it means we are in the middle of a path. In the middle of a path we operate on ERC20 only.
    if (payer == address(this)) {
      IERC20(token).safeTransfer(recipient, value);
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
      } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
      }
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
