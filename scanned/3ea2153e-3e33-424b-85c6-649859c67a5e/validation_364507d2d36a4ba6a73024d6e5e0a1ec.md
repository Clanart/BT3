### Title
Router ETH Balance Used as WETH Payment Source Regardless of `payer` Identity, Enabling Theft of Stranded ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` silently ignores the `payer` argument whenever the router holds native ETH and `token == WETH`. Any ETH left in the router by a prior user (e.g., from an `exactOutputSingle` overpayment without a `refundETH` call) can be consumed by any subsequent WETH-input swap caller, who receives the swap output without spending any of their own tokens.

---

### Finding Description

In `PeripheryPayments.pay()`:

```solidity
function pay(address token, address payer, address recipient, uint256 value) internal {
    if (payer == address(this)) {
        IERC20(token).safeTransfer(recipient, value);
    } else if (token == WETH) {
        uint256 nativeBalance = address(this).balance;
        if (nativeBalance >= value) {
            IWETH9(WETH).deposit{value: value}();   // ← uses router's ETH, not payer's
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
``` [1](#0-0) 

When `nativeBalance >= value`, the `payer` is never consulted. The router wraps its own ETH balance and forwards it to the pool. The `payer` stored in transient context (always `msg.sender` of the swap entry point) is irrelevant in this branch. [2](#0-1) 

The transient context sets `payer = msg.sender`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
``` [3](#0-2) 

Then `_justPayCallback` calls `pay(_getTokenToPay(), _getPayer(), msg.sender, ...)`. If `tokenIn == WETH` and the router has ETH, the `_getPayer()` value is never used.

The same flaw exists in `MetricOmmPoolLiquidityAdder.metricOmmModifyLiquidityCallback()` when `token0 == WETH`: [4](#0-3) 

ETH enters the router legitimately via `msg.value` on any `payable` entry point (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`, `multicall`, `addLiquidityExactShares`, `addLiquidityWeighted`). The `receive()` guard only blocks *direct* ETH pushes from non-WETH addresses; it does not prevent ETH from accumulating via `msg.value` overpayment. [5](#0-4) 

---

### Impact Explanation

**Direct loss of user ETH.** A victim who sends excess ETH (e.g., via `exactOutputSingle{value: X}` where X exceeds the actual input required) and omits `refundETH` leaves ETH stranded in the router. An attacker who calls `exactInputSingle` with `tokenIn = WETH` and `amountIn ≤ stranded balance` receives the full swap output without spending any WETH or ETH of their own. The victim's ETH is permanently consumed.

This is the direct analog of the seeded bug: just as any holder of external LP tokens could drain protocol rewards without having deposited through `Omnipool.deposit()`, any caller of a WETH swap can drain the router's ETH balance without having contributed that ETH — because in both cases an unverified external balance (external LP holdings / router's `address(this).balance`) is used as the payment source instead of the tracked depositor/payer.

---

### Likelihood Explanation

**Medium.** The `exactOutputSingle` pattern — where the exact input is unknown in advance and users send a conservative ETH overpayment — is the primary intended use of native ETH input. The documentation and tests show `refundETH` as a recommended but optional follow-up step: [6](#0-5) 

Users who omit `refundETH` (a common mistake) leave ETH exploitable. An attacker needs only to watch the router's ETH balance on-chain and call a WETH swap in the next block.

---

### Recommendation

Track the ETH contributed by the current call in transient storage (e.g., store `msg.value` at entry and decrement it as it is consumed in `pay()`). In `pay()`, replace `address(this).balance` with the per-call ETH budget so that only the ETH the current caller sent can be used to fund their swap. Alternatively, enforce that `refundETH` is always called in the same multicall by checking that `address(this).balance == 0` at the end of `multicall`.

---

### Proof of Concept

```
1. Victim calls exactOutputSingle{value: 1_000}(amountOut=X, tokenIn=WETH, amountInMaximum=1_000)
   - Actual input required: 600 ETH
   - pay(WETH, victim, pool, 600): nativeBalance=1000 >= 600 → wraps 600 ETH, sends to pool
   - Router ETH balance after: 400 (stranded, victim forgot refundETH)

2. Attacker calls exactInputSingle(amountIn=400, tokenIn=WETH) — no ETH sent, no WETH approval
   - _setNextCallbackContext(pool, JUST_PAY, attacker, WETH)
   - Pool executes swap, calls metricOmmSwapCallback
   - _justPayCallback → pay(WETH, attacker, pool, 400)
   - nativeBalance=400 >= 400 → wraps router's 400 ETH, sends to pool
   - Attacker receives swap output tokens; payer=attacker is never consulted

Result: Victim loses 400 ETH. Attacker receives output tokens for free.
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L192-198)
```text
  function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
      _getTokenToPay(),
      _getPayer(),
      msg.sender,
      uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-177)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
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
