The code confirms all claims. Let me verify the key details before rendering the final output.

Audit Report

## Title
Unguarded `refundETH`, `sweepToken`, and `unwrapWETH9` Allow Any Caller to Drain Router ETH/Token Balances — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.refundETH`, `sweepToken`, and `unwrapWETH9` carry no `msg.sender` authorization check. Because `exactInputSingle` and `exactInput` are `payable` and the `pay` helper wraps only the pool-requested amount of native ETH (leaving any surplus on the contract), an attacker can front-run or race the user's cleanup call and redirect the stranded ETH or token balance to themselves, causing a direct loss of user principal.

## Finding Description
`refundETH()` unconditionally sends `address(this).balance` to `msg.sender` with no check that the caller is the original depositor. [1](#0-0) 

`sweepToken` and `unwrapWETH9` similarly accept a caller-supplied `recipient` with no authorization, transferring the router's full ERC-20 or WETH balance to any address the attacker chooses. [2](#0-1) 

`exactInputSingle` is `payable` and does not enforce `msg.value == params.amountIn`. [3](#0-2) 

Inside the swap callback, `pay` wraps exactly the pool-requested `value` of native ETH and forwards it; any `msg.value` surplus remains as raw ETH on the contract. [4](#0-3) 

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) does not protect against this: it is only invoked for plain ETH transfers with no calldata, not for `msg.value` attached to a payable function call. [5](#0-4) 

The intended safe usage pattern (confirmed by the test suite and the NatDev comment) is to batch `exactInputSingle` + `refundETH` atomically inside a single `multicall` call. However, there is no on-chain enforcement of this requirement. [6](#0-5) [7](#0-6) 

A user who calls `exactInputSingle` directly (outside of `multicall`) with `msg.value > amountIn` leaves the difference on the router between their swap transaction and any subsequent `refundETH` call. Any third party who calls `refundETH()` first receives the stranded ETH. The same race applies to `sweepToken` for any ERC-20 balance left on the router (e.g., intermediate tokens in multi-hop `exactInput` if a partial-fill revert path is ever reached), and to `unwrapWETH9` for WETH balances directed to `address(this)` as the swap recipient. [8](#0-7) 

## Impact Explanation
Direct loss of user principal: surplus native ETH sent with a swap call is permanently redirectable to an attacker with a single permissionless call. No special role or privilege is required. The lost amount equals `msg.value − amountIn` per affected transaction, with no recovery path once the attacker's transaction is confirmed. This satisfies the "Critical/High direct loss of user principal" impact gate.

## Likelihood Explanation
- `exactInputSingle` and `exactInput` are `payable`; users who do not use `multicall` (or who over-send ETH to account for uncertain pool consumption) leave ETH on the contract between transactions.
- The router is a shared singleton; any stranded ETH is visible on-chain and trivially front-runnable by MEV bots scanning for non-zero `address(router).balance`.
- No special role, permission, or setup is required; the attack is a single external call (`refundETH()`).
- The attack is repeatable across every affected user transaction.

## Recommendation
Restrict `refundETH`, `sweepToken`, and `unwrapWETH9` so they can only be invoked as part of an atomic `multicall` (e.g., require `msg.sender == address(this)` enforced via a `delegatecall`-only modifier), or record the depositor address in transient storage at the start of each top-level payable entry point and validate it inside each cleanup function. The simplest correct fix is a `onlySelf` modifier that reverts unless the call arrives via `delegatecall` from `multicall`, ensuring the refund/sweep/unwrap step is always atomic with the swap that created the balance.

## Proof of Concept
1. User calls `router.exactInputSingle{value: 2e18}(ExactInputSingleParams{tokenIn: WETH, amountIn: 1e18, ...})` directly (not via `multicall`).
2. Inside the swap callback, `pay` executes `IWETH9(WETH).deposit{value: 1e18}()` and forwards the WETH to the pool. The remaining `1e18` ETH stays at `address(router)`.
3. Attacker observes the pending transaction in the mempool and submits `router.refundETH()` with higher gas priority.
4. `refundETH` executes: `balance = address(this).balance == 1e18 > 0`, so `_transferETH(msg.sender /*attacker*/, 1e18)` runs.
5. Attacker receives `1e18` ETH. The user's subsequent `refundETH()` call (if made) finds zero balance and returns nothing.

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L57-63)
```text
  /// @inheritdoc IPeripheryPayments
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-78)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L8-10)
```text
/// @dev Native ETH flows follow Uniswap v3-periphery multicall patterns:
///      - ETH input: multicall{value}(exactInput*) with WETH as tokenIn
///      - ETH output: swap WETH to router, then unwrapWETH9 in the same multicall
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
