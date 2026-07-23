### Title
Unguarded `refundETH`, `sweepToken`, and `unwrapWETH9` Allow Any Caller to Drain Router ETH/Token Balances — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.refundETH`, `sweepToken`, and `unwrapWETH9` carry no caller-authorization check. Any address can invoke them at any time. Because the router legitimately holds a native-ETH balance between the moment a user sends `msg.value` and the moment they call the cleanup function, an attacker can front-run or race the cleanup call and drain the excess ETH (or any ERC-20 / WETH balance) to themselves.

---

### Finding Description

`PeripheryPayments` exposes three `public`/`external payable` balance-draining helpers with no `msg.sender` guard:

```
refundETH()          → sends address(this).balance to msg.sender (anyone)
sweepToken(token, min, recipient) → sends full ERC-20 balance to arbitrary recipient
unwrapWETH9(min, recipient)       → unwraps full WETH balance to arbitrary recipient
``` [1](#0-0) [2](#0-1) 

The router's `pay` helper, when `tokenIn == WETH`, wraps **exactly the pool-requested amount** of native ETH and forwards it; any surplus `msg.value` is left as raw ETH on the contract:

```solidity
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();   // wraps only `value`, not all ETH
    IERC20(WETH).safeTransfer(recipient, value);
}
``` [3](#0-2) 

`exactInputSingle` and `exactInput` are `payable` and set the payer to `msg.sender` with `tokenIn` as the token to pay; they do **not** enforce `msg.value == params.amountIn`. A user who sends `msg.value > amountIn` (common when the exact pool consumption is unknown in advance, or when using a price-limit that causes partial fill) leaves the difference on the contract. [4](#0-3) 

Because `refundETH()` sends `address(this).balance` to `msg.sender` with no check that the caller is the original depositor, any third party who calls it first receives the stranded ETH.

Similarly, `sweepToken` and `unwrapWETH9` accept a caller-supplied `recipient` with no authorization, so any ERC-20 or WETH balance that accumulates on the router (e.g., from intermediate multi-hop hops where `address(this)` is the intermediate recipient) can be redirected to an attacker. [5](#0-4) 

The analog to the external report is exact: the missing `require(msg.sender == originalDepositor)` guard (the "commented-out check" in TokenDistribution.sol) is simply absent here — anyone can perform the cleanup action and redirect the funds.

---

### Impact Explanation

A user who calls `exactInputSingle` (or `exactInput`) with `msg.value` exceeding the actual pool consumption loses the surplus ETH to the first caller of `refundETH()`. Similarly, any ERC-20 balance left on the router after a multi-hop `exactInput` (intermediate tokens temporarily held at `address(this)`) can be swept by an attacker via `sweepToken`. This is a direct loss of user principal with no recovery path once the attacker's transaction is confirmed.

---

### Likelihood Explanation

- `exactInputSingle` and `exactInput` are `payable`; users routinely over-send ETH when the exact consumption depends on pool state at execution time.
- The router is deployed as a shared singleton; any ETH left between a user's swap call and their `refundETH` call is visible on-chain and can be front-run by MEV bots.
- No special role or permission is required; the attack is permissionless and requires only a single external call.

---

### Recommendation

Restrict `refundETH`, `sweepToken`, and `unwrapWETH9` so that only the address that initiated the current multicall (or an explicitly authorized caller) can invoke them, or enforce that `msg.value` is consumed in full within the same atomic call. The simplest fix is to require these helpers to be called only via `multicall` (i.e., only when `msg.sender == address(this)` via `delegatecall`), or to record the depositor in transient storage and validate it in each cleanup function.

---

### Proof of Concept

1. User calls `exactInputSingle({tokenIn: WETH, amountIn: 1e18, ...})` with `msg.value = 2e18` (over-sending by 1 ETH).
2. The pool callback fires; `pay` wraps exactly `1e18` ETH into WETH and sends it to the pool. The remaining `1e18` ETH stays at `address(router)`.
3. Attacker observes the pending transaction in the mempool, submits `router.refundETH()` with higher gas.
4. `refundETH` executes: `address(this).balance == 1e18 > 0`, so `_transferETH(msg.sender /*attacker*/, 1e18)` runs.
5. Attacker receives 1 ETH; user's `refundETH` call (if they even made one) finds zero balance and returns nothing. [1](#0-0) [3](#0-2)

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
