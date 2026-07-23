Audit Report

## Title
Unguarded `refundETH`, `sweepToken`, and `unwrapWETH9` Allow Any Caller to Drain Router ETH/Token Balances — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.refundETH`, `sweepToken`, and `unwrapWETH9` carry no `msg.sender` authorization check. The router legitimately accumulates surplus native ETH when a user sends `msg.value` exceeding the pool-consumed amount (most commonly via `exactOutputSingle` with a slippage buffer, or `exactInputSingle` with a price-limit partial fill). Any third party who calls `refundETH()` before the original depositor receives the entire stranded ETH balance. Similarly, any ERC-20 or WETH balance temporarily held by the router (e.g., when `recipient = address(this)` for an intermediate unwrap step) can be redirected to an attacker via `sweepToken` or `unwrapWETH9`.

## Finding Description

**Root cause — no authorization on balance-draining helpers:**

`refundETH` unconditionally sends `address(this).balance` to `msg.sender`: [1](#0-0) 

`sweepToken` and `unwrapWETH9` accept a caller-supplied `recipient` with no check that the caller is the depositor: [2](#0-1) 

**How surplus ETH accumulates on the router:**

The `pay` helper, when `token == WETH`, wraps *exactly* the pool-requested `value` from `address(this).balance`, leaving any excess `msg.value` as raw ETH on the contract: [3](#0-2) 

**Trigger paths:**

1. `exactOutputSingle` is `external payable`. The user does not know the exact ETH needed; they send `msg.value = amountInMaximum` as a slippage buffer. The pool callback fires and `pay()` wraps only `actualAmountIn ≤ amountInMaximum`. The remainder (`amountInMaximum − actualAmountIn`) stays on the router until `refundETH` is called: [4](#0-3) 

2. `exactInputSingle` is `external payable` and does not enforce `msg.value == params.amountIn`. If a `priceLimitX64` causes a partial fill, the pool callback requests only `actualAmountIn < amountIn`; `pay()` wraps that smaller amount and the surplus stays on the router: [5](#0-4) 

**Existing guards are insufficient:**

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only blocks plain ETH transfers; it does not apply to `msg.value` attached to `payable` function calls: [6](#0-5) 

The `multicall` function (the intended atomic pattern) uses `delegatecall` and shares `msg.sender`, making it safe when used correctly. However, `refundETH`, `sweepToken`, and `unwrapWETH9` are independently callable as standalone external functions with no enforcement that they must be invoked through `multicall`: [7](#0-6) 

The project's own tests confirm the intended pattern is `multicall([swap, refundETH])` for atomicity, but nothing in the contract enforces this: [8](#0-7) 

## Impact Explanation
A user who calls `exactOutputSingle` (or `exactInputSingle` with a price limit) with `tokenIn = WETH` and sends `msg.value > actualAmountIn` loses the surplus ETH to the first caller of `refundETH`. Similarly, any WETH or ERC-20 balance left on the router (e.g., when `recipient = address(this)` for a two-step unwrap) can be redirected to an attacker via `unwrapWETH9` or `sweepToken`. This is a direct, unrecoverable loss of user principal with no protocol-level mitigation once the attacker's transaction is confirmed. Severity: **High** — permissionless theft of user funds with no special role required.

## Likelihood Explanation
- `exactOutputSingle` is the primary exact-output path; users routinely send `msg.value = amountInMaximum` because the exact pool consumption is unknown at submission time.
- The router is a shared singleton; any ETH left between a user's swap call and their `refundETH` call is visible on-chain and trivially front-runnable by MEV bots.
- No special role, permission, or setup is required; the attack is a single external call.
- The project's own payment tests confirm `refundETH` sends the full balance to any caller: [9](#0-8) 

## Recommendation
Restrict `refundETH`, `sweepToken`, and `unwrapWETH9` so they can only be invoked through `multicall` (i.e., require `msg.sender == address(this)` enforced via the `delegatecall` context), or record the depositor in transient storage at swap entry and validate it in each cleanup function. The simplest atomic fix is to add a `onlySelf` modifier (checking `msg.sender == address(this)`) to all three helpers, forcing users to bundle them inside `multicall` where `msg.sender` is preserved as the original EOA.

## Proof of Concept
1. User calls `exactOutputSingle({tokenIn: WETH, amountOut: X, amountInMaximum: 2e18, ...})` with `msg.value = 2e18`.
2. Pool callback fires; `pay()` wraps exactly `actualAmountIn < 2e18` ETH and forwards it to the pool. The remaining `2e18 − actualAmountIn` ETH stays at `address(router)`.
3. Attacker observes the pending transaction in the mempool, submits `router.refundETH()` with higher gas.
4. `refundETH` executes: `balance = 2e18 − actualAmountIn > 0`, so `_transferETH(msg.sender /*attacker*/, balance)` runs.
5. Attacker receives the surplus ETH; user's subsequent `refundETH` call finds zero balance.

Foundry test skeleton:
```solidity
function test_frontrun_refundETH() public {
    uint128 amountOut = 1_000;
    uint256 amountInMax = 2 ether;

    vm.prank(user);
    router.exactOutputSingle{value: amountInMax}(
        IMetricOmmSimpleRouter.ExactOutputSingleParams({
            pool: address(pool), tokenIn: address(weth), tokenOut: address(token1),
            zeroForOne: true, amountOut: amountOut, amountInMaximum: uint128(amountInMax),
            recipient: user, deadline: block.timestamp + 1, priceLimitX64: 0, extensionData: ""
        })
    );
    // Surplus ETH is now on the router
    uint256 surplus = address(router).balance;
    assertGt(surplus, 0);

    uint256 attackerBefore = attacker.balance;
    vm.prank(attacker);
    router.refundETH(); // attacker steals surplus
    assertEq(attacker.balance - attackerBefore, surplus);
    assertEq(address(router).balance, 0);
}
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L106-133)
```text
  function test_multicall_ethInput_exactInputSingle_refundsUnusedEth() public {
    uint128 amountIn = 1_000;
    uint256 msgValue = 2 ether;
    uint256 swapperEthBefore = swapper.balance;

    vm.prank(swapper);
    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeWithSelector(
      router.exactInputSingle.selector,
      IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: recipient,
        deadline: _deadline(),
        priceLimitX64: 0,
        extensionData: ""
      })
    );
    calls[1] = abi.encodeWithSelector(router.refundETH.selector);
    router.multicall{value: msgValue}(calls);

    assertEq(swapper.balance, swapperEthBefore - amountIn, "unused eth refunded");
    _assertRouterEmpty();
  }
```

**File:** metric-periphery/test/MetricOmmSimpleRouter.payments.t.sol (L74-85)
```text
  function test_refundETH_sendsBalanceToCaller() public {
    uint256 amount = 2 ether;
    vm.deal(address(router), amount);

    uint256 swapperBefore = swapper.balance;

    vm.prank(swapper);
    router.refundETH();

    assertEq(swapper.balance - swapperBefore, amount, "swapper refunded");
    assertEq(address(router).balance, 0, "router eth cleared");
  }
```
