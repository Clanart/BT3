### Title
Unrestricted `sweepToken` and `unwrapWETH9` Allow Any Caller to Drain Router-Held Balances to an Arbitrary Recipient - (File: metric-periphery/contracts/base/PeripheryPayments.sol)

### Summary
`sweepToken` and `unwrapWETH9` in `PeripheryPayments` are `public` with no caller restriction and accept a fully attacker-controlled `recipient` address. Any tokens or WETH that land on the router between transactions can be drained to any address by any unprivileged caller.

### Finding Description
`PeripheryPayments.sweepToken` and `PeripheryPayments.unwrapWETH9` are both `public payable` and impose no check on `msg.sender`:

```solidity
// PeripheryPayments.sol L48-55
function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);
    if (balanceToken > 0) {
        IERC20(token).safeTransfer(recipient, balanceToken);
    }
}

// PeripheryPayments.sol L37-45
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);
    if (balanceWETH > 0) {
        IWETH9(WETH).withdraw(balanceWETH);
        _transferETH(recipient, balanceWETH);
    }
}
``` [1](#0-0) 

The `pay()` internal function, called from every swap callback, uses the router's native ETH balance to partially or fully cover WETH payments regardless of which user deposited that ETH:

```solidity
// PeripheryPayments.sol L73-84
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    }
``` [2](#0-1) 

`exactInputSingle` is `payable` and passes `msg.sender` as payer only when `token == WETH`; if the price limit causes a partial fill, the unused portion of `msg.value` stays on the router with no attribution: [3](#0-2) 

`refundETH()` sends the entire router ETH balance to `msg.sender` with no restriction: [4](#0-3) 

### Impact Explanation
Any ETH or ERC-20 balance that lands on the router — from excess `msg.value` on a price-limited WETH swap, from a standalone (non-multicall) swap with `recipient: address(router)`, or from any other source — can be immediately stolen by an unprivileged attacker calling `sweepToken(token, 0, attacker)`, `unwrapWETH9(0, attacker)`, or `refundETH()`. The attacker controls the `recipient` parameter entirely; no role, allowance, or prior interaction is required. Loss is bounded only by whatever balance happens to be on the router, which can be arbitrarily large.

### Likelihood Explanation
The router is `payable` on every swap entry point. A user who calls `exactInputSingle` directly (not via `multicall`) with `msg.value > amountIn`, or whose swap is partially filled by a price limit, will strand ETH on the router. A MEV bot monitoring the mempool can front-run the user's intended `refundETH` call or simply call `sweepToken`/`unwrapWETH9` in the next block. No special privilege or setup is required.

### Recommendation
Restrict `sweepToken`, `unwrapWETH9`, and `refundETH` so that the `recipient` is always `msg.sender` (removing the caller-controlled parameter), or add an `onlyAuthorized` guard that tracks which address deposited which balance. At minimum, `refundETH` already sends to `msg.sender`, so the analogous fix for `sweepToken` and `unwrapWETH9` is to remove the `recipient` parameter and always transfer to `msg.sender`.

### Proof of Concept

```
// Attacker steals User A's stranded WETH

// Step 1 – User A calls exactInputSingle directly (no multicall) with tokenIn=WETH,
//           amountIn=1_000, msg.value=2_000. Swap uses 1_000 ETH; 1_000 ETH stays on router.
vm.prank(userA);
router.exactInputSingle{value: 2_000}(ExactInputSingleParams({
    pool: pool,
    tokenIn: WETH,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1_000,
    amountOutMinimum: 0,
    recipient: userA,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// router.balance == 1_000 (stranded)

// Step 2 – Attacker calls refundETH() and receives userA's 1_000 ETH.
vm.prank(attacker);
router.refundETH();
assertEq(attacker.balance, 1_000);

// Alternatively, if WETH output was sent to the router:
// vm.prank(attacker);
// router.sweepToken(address(weth), 0, attacker);
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L37-55)
```text
  function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);

    if (balanceWETH > 0) {
      IWETH9(WETH).withdraw(balanceWETH);
      _transferETH(recipient, balanceWETH);
    }
  }

  /// @inheritdoc IPeripheryPayments
  function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);

    if (balanceToken > 0) {
      IERC20(token).safeTransfer(recipient, balanceToken);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-84)
```text
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
