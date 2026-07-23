Audit Report

## Title
Router-held native ETH residue silently consumed by subsequent user's WETH payment — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

The `pay` function in `PeripheryPayments` uses `address(this).balance` — the router's entire native ETH balance — when satisfying a WETH payment obligation. Because the payable entry points (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`, `multicall`) accept `msg.value` without tracking per-caller ETH budgets, any ETH a prior user sent but did not recover via `refundETH()` remains on the router and is silently spent on behalf of the next user who swaps with `tokenIn = WETH`. The prior user's ETH is permanently lost; the next user pays nothing for their WETH input.

## Finding Description

`PeripheryPayments.pay` (lines 73–84) handles the WETH leg by reading `address(this).balance`:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ALL router ETH, any source
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

`address(this).balance` is not scoped to the current caller's `msg.value`. It is the aggregate of every wei that has ever arrived on the router and not yet been swept.

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only applies to bare ETH transfers with no calldata. It does **not** prevent ETH from accumulating via `msg.value` in the payable entry points (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`, `multicall`). [2](#0-1) 

None of the entry points track `msg.value` or enforce that `address(this).balance == 0` before execution. The callback context stored via `_setNextCallbackContext` records the payer address and token, but not the ETH amount the current caller contributed. [3](#0-2) 

`refundETH()` sends the entire balance to `msg.sender`, not to the original depositor, so stranded ETH is not automatically returned and is accessible to any caller. [4](#0-3) 

**Exploit path:**

1. Victim calls `exactInputSingle{value: 2000}(tokenIn=WETH, amountIn=1000, ...)`. `pay` deposits 1000 ETH as WETH and pays the pool. 1000 ETH remains on the router. Victim omits `refundETH()`.
2. Attacker calls `exactInputSingle{value: 0}(tokenIn=WETH, amountIn=1000, ...)`. Inside `_justPayCallback`, `pay` reads `address(this).balance == 1000`, deposits the victim's ETH as WETH, and pays the pool. Attacker receives full swap output at zero cost. [5](#0-4) 

## Impact Explanation

Direct, permanent loss of victim's native ETH principal. The loss amount equals the stranded ETH balance at the time of the attacker's call, which is unbounded. No privileged access is required; any public swap call with `tokenIn = WETH` and `msg.value = 0` triggers the vulnerable path. This constitutes a critical direct loss of user principal.

## Likelihood Explanation

Stranding ETH on the router is a natural user mistake: `exactOutputSingle` with `msg.value > actual amountIn`, or a multicall that omits `refundETH()`. The `multicall` function applies no per-call ETH accounting. An attacker monitoring the mempool or the router's ETH balance can front-run or back-run any transaction that leaves ETH behind. No special permissions or setup are required. [6](#0-5) 

## Recommendation

Track the per-call ETH budget explicitly. Record `msg.value` at the top of each public entry point (or in `multicall`) and pass it as a parameter to `pay`, so that only the current caller's ETH contribution is eligible for WETH wrapping. Auto-refund any remaining ETH at the end of each call rather than relying on the user to include a separate `refundETH()` step. Alternatively, enforce that `address(this).balance == 0` at the start of every non-multicall entry point and auto-refund at the end of `multicall`.

## Proof of Concept

```
State before:
  router.balance = 0
  victim.balance = 2000
  attacker.balance = 0

Step 1 – victim swaps 1000 ETH worth of WETH, sends 2000 ETH, forgets refundETH():
  victim calls exactInputSingle{value: 2000}(tokenIn=WETH, amountIn=1000)
  → pay() sees nativeBalance=2000 >= 1000
  → deposits 1000 ETH as WETH, transfers to pool
  → router.balance = 1000  (victim's residue, unrecovered)

Step 2 – attacker steals residue:
  attacker calls exactInputSingle{value: 0}(tokenIn=WETH, amountIn=1000)
  → pay() sees nativeBalance=1000 >= 1000
  → deposits 1000 ETH (victim's!) as WETH, transfers to pool
  → attacker receives full swap output, pays nothing

Final state:
  router.balance = 0
  victim.balance = 0   (lost 2000 ETH total)
  attacker received swap output worth ~1000 ETH at zero cost
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L192-199)
```text
  function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
      _getTokenToPay(),
      _getPayer(),
      msg.sender,
      uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
  }
```
