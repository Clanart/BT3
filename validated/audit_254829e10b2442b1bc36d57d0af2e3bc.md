### Title
Unattributed Router ETH Balance Allows Any Caller to Steal Stranded Native ETH via `refundETH` or Execute Free WETH Swaps via `pay` — (`File: metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` settles WETH swaps by consuming `address(this).balance` (the router's total ETH) without verifying that the ETH belongs to the current payer. `refundETH()` sends the router's entire ETH balance to `msg.sender` with no caller restriction. Any ETH stranded in the router — which occurs whenever a user calls a payable swap function with `msg.value` exceeding the actual swap cost outside of a `refundETH`-inclusive multicall — is immediately claimable by any third party, either as a direct ETH theft or as a free WETH swap.

---

### Finding Description

`PeripheryPayments.pay()` handles WETH settlement with the following logic:

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
```

The function reads `address(this).balance` — the router's **total** ETH balance — and uses it to pay for the current swap without any attribution to the current payer. If the router holds ETH from a prior user's transaction, that ETH is silently consumed on behalf of the current caller, who pays nothing.

ETH strands in the router whenever a user calls any payable swap entry point (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`) with `msg.value` exceeding the actual swap cost, without including `refundETH` in the same multicall. For `exactOutputSingle` in particular, the exact `amountIn` is unknown before execution, so users naturally send `msg.value = amountInMaximum` as a buffer. The actual `amountIn` is typically less, leaving `amountInMaximum - amountIn` ETH stranded.

`refundETH()` compounds this:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
```

It sends the router's **entire** ETH balance to `msg.sender` with no access control. Any caller — not just the user who deposited the ETH — can invoke it and receive all stranded ETH.

---

### Impact Explanation

Two concrete loss paths exist:

**Path A — Direct ETH theft via `refundETH`:**
Alice calls `exactOutputSingle{value: amountInMaximum}` directly (not via multicall). The swap uses `amountIn < amountInMaximum`. The difference `amountInMaximum - amountIn` remains in the router. Bob immediately calls `refundETH()` and receives Alice's entire stranded ETH balance. Alice loses `amountInMaximum - amountIn` ETH with no recourse.

**Path B — Free WETH swap via `pay`:**
With the same stranded ETH in the router, Bob calls `exactInputSingle(amountIn = N, tokenIn = WETH)` sending zero ETH and holding zero WETH. The callback invokes `pay(WETH, Bob, pool, N)`. Since `address(this).balance >= N`, the router deposits Alice's ETH as WETH and transfers it to the pool. Bob receives output tokens without paying anything. Alice's ETH is consumed.

Both paths result in direct, unrecoverable loss of user principal. The loss magnitude equals the excess ETH sent by the victim, which for `exactOutputSingle` can be as large as `amountInMaximum - amountIn`.

---

### Likelihood Explanation

The trigger condition — a user calling a payable swap function with excess `msg.value` outside a `refundETH`-inclusive multicall — is a natural and common usage pattern. The interface comment on `IMetricOmmSimpleRouter` explicitly notes the native ETH pattern (`multicall{value}(exactInput*)`) but does not enforce it. Users calling `exactOutputSingle` directly with ETH (as shown in `test_mixedNativeAndWeth_exactOutputSingle_wethForToken`) will strand ETH whenever `msg.value != amountIn`. An attacker can monitor the mempool for such transactions and front-run or immediately follow with a `refundETH` call. No special privileges are required.

---

### Recommendation

1. **Track per-call ETH attribution**: Record `msg.value` at the start of each payable entry point in transient storage. In `pay`, consume only up to the recorded `msg.value` for the current call, not `address(this).balance`.

2. **Restrict `refundETH` to multicall context or track the depositor**: Either restrict `refundETH` so it can only be called by the address that deposited ETH in the same multicall, or track the depositor in transient storage and enforce it in `refundETH`.

3. **Alternatively, enforce the multicall pattern**: Make all payable swap entry points non-payable and require ETH input only through a dedicated `wrapAndDeposit` step within multicall, so ETH is always wrapped before the swap callback fires.

---

### Proof of Concept

```
State: router.balance == 0

Step 1 — Alice strands ETH:
  Alice calls exactOutputSingle{value: 10_000}(
    tokenIn: WETH, amountOut: 500, amountInMaximum: 10_000
  )
  → actual amountIn = 600 (determined by pool)
  → pay() uses 600 ETH from msg.value, deposits as WETH, sends to pool
  → router.balance = 10_000 - 600 = 9_400 ETH (stranded)
  → Alice receives 500 output tokens

Step 2a — Bob steals ETH directly:
  Bob calls refundETH()
  → refundETH sends address(this).balance = 9_400 to Bob
  → Alice loses 9_400 ETH

Step 2b — Bob executes free swap (alternative):
  Bob calls exactInputSingle(amountIn: 9_400, tokenIn: WETH) with msg.value = 0
  → callback: pay(WETH, Bob, pool, 9_400)
  → nativeBalance = 9_400 >= 9_400 → deposits Alice's ETH as WETH, sends to pool
  → Bob receives output tokens worth ~9_400 ETH, pays nothing
  → Alice loses 9_400 ETH
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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
