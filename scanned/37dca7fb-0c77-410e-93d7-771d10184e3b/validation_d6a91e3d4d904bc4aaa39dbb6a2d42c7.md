### Title
Router `pay()` Consumes Unattributed Native ETH Balance, Enabling Cross-Transaction Residue Theft of Stranded WETH-Input ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` helper in `PeripheryPayments.sol` uses `address(this).balance` — the router's **entire** native ETH balance — to cover WETH payment obligations for any external payer, with no per-user attribution. When a user sends `msg.value` that exceeds the swap's actual WETH need and omits (or delays) a `refundETH` call, the surplus ETH is stranded on the router. A subsequent caller whose `tokenIn` is WETH can execute a swap that is silently funded by the stranded ETH, draining the first user's principal without their consent.

---

### Finding Description

`pay()` branches on `token == WETH` and reads `address(this).balance` at call time:

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

`address(this).balance` is the **aggregate** native balance of the router contract. It is not scoped to the current caller's `msg.value`. Any ETH left on the router from a prior transaction — because a user sent excess `msg.value` without a `refundETH` step — is indistinguishable from the current caller's ETH and will be consumed first.

The `receive()` guard blocks direct ETH pushes:

```solidity
receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
}
``` [2](#0-1) 

However, `msg.value` attached to any `payable` entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`) bypasses `receive()` entirely and lands directly in `address(this).balance`. If the swap consumes less than `msg.value`, the surplus persists on the router across transaction boundaries.

`refundETH` is the intended recovery path, but it is a separate public call:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
``` [3](#0-2) 

If the victim submits `refundETH` in a separate transaction (rather than atomically in the same `multicall`), an attacker can front-run it.

The swap entry points set the transient callback context and then call the pool:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool).swap(...);
``` [4](#0-3) 

The pool calls back into `metricOmmSwapCallback`, which calls `_justPayCallback`, which calls `pay()` with the attacker as `payer`. Because `address(this).balance` already holds the victim's stranded ETH, the `nativeBalance >= value` branch fires and the attacker's WETH obligation is settled entirely from the victim's ETH — the attacker's wallet is not touched.

---

### Impact Explanation

**Direct loss of user principal.** The victim loses the full stranded ETH amount (up to `msg.value − amountIn` per transaction). The attacker receives an equivalent WETH swap at zero cost. There is no cap on the stolen amount other than the victim's stranded balance. This satisfies the "Critical/High/Medium direct loss of user principal above Sherlock thresholds" gate.

---

### Likelihood Explanation

**Medium.** The precondition — ETH stranded on the router — arises whenever a user:
- sends `msg.value > amountIn` to a WETH-input swap without atomically including `refundETH` in the same `multicall`, or
- submits `refundETH` as a separate follow-up transaction.

Both patterns are common in practice (users over-provision ETH to avoid reverts, or compose calls naively). An attacker needs only to watch the mempool for transactions that leave a non-zero `address(router).balance` and submit a WETH-input swap before the victim's `refundETH` lands.

---

### Recommendation

1. **Atomic refund enforcement**: Document and enforce (via NatSpec and integration guides) that every WETH-input swap with `msg.value` **must** include `refundETH` as the final step of the same `multicall`. This is the Uniswap v3 pattern and eliminates the cross-transaction window.

2. **Scoped native balance tracking**: Alternatively, track the current call's `msg.value` contribution separately (e.g., pass it as a parameter or store it in transient storage at entry) and limit `pay()`'s native ETH consumption to that scoped amount, reverting or pulling from the payer's WETH allowance for any excess.

3. **Guard in `pay()`**: When `payer != address(this)` and `token == WETH`, cap the native ETH consumed to `min(nativeBalance, value)` and always pull the remainder from `payer` via `safeTransferFrom`, never silently consuming unattributed router balance.

---

### Proof of Concept

```
Block N:
  Victim calls exactInputSingle{value: 2000}(
      pool=WETH/TOKEN pool,
      tokenIn=WETH,
      amountIn=1000,          // only 1000 needed
      ...
  )
  → pay() fires: nativeBalance=2000 >= value=1000
    → deposits 1000 ETH as WETH, transfers to pool ✓
    → 1000 ETH remains on router (address(router).balance = 1000)
  Victim does NOT include refundETH in this call.

Block N (same or next):
  Attacker observes address(router).balance = 1000 ETH in mempool.
  Attacker front-runs victim's pending refundETH with:

  Attacker calls exactInputSingle{value: 0}(
      pool=WETH/TOKEN pool,
      tokenIn=WETH,
      amountIn=1000,
      ...
  )
  → _setNextCallbackContext(pool, JUST_PAY, attacker, WETH)
  → pool.swap() executes, calls metricOmmSwapCallback
  → _justPayCallback → pay(WETH, attacker, pool, 1000)
  → nativeBalance = 1000 >= value = 1000
  → deposits 1000 ETH (victim's) as WETH, transfers to pool
  → attacker's wallet: 0 WETH spent, receives TOKEN output ✓

  Victim calls refundETH:
  → address(router).balance = 0
  → victim receives 0 ETH back
  → victim net loss: 1000 ETH
```

The attacker receives a full WETH→TOKEN swap funded entirely by the victim's stranded ETH. The victim's `refundETH` call returns nothing. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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
