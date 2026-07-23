The vulnerability is real. Let me trace the exact path through the code.

**Step 1 — ETH stranding:** `exactInputSingle` is `payable`. A victim can call it with `msg.value > amountIn` (e.g., `{value: 1 ETH}` but `amountIn = 0.5 ETH`). The `pay` function consumes only what the pool demands; the excess ETH is never automatically refunded and sits on the router. [1](#0-0) 

**Step 2 — Attacker's swap:** Attacker calls `exactInputSingle{value:0}(tokenIn=WETH, amountIn=X)`. The callback context is set with `payer = msg.sender` (attacker) and `tokenToPay = WETH`. [2](#0-1) 

**Step 3 — `pay` uses stranded ETH:** In `_justPayCallback`, `pay(WETH, attacker, pool, X)` is called. The WETH branch checks `address(this).balance` — which includes the victim's stranded ETH — and if `nativeBalance >= value`, wraps it and transfers it to the pool, never touching the attacker's WETH allowance. [3](#0-2) 

The `receive()` guard (line 33) only blocks direct ETH sends from non-WETH addresses; it does not prevent ETH from entering via `msg.value` on payable functions. [4](#0-3) 

---

### Title
Stranded ETH on router subsidizes attacker's WETH swap via `pay()` native-balance priority — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.pay()` unconditionally uses the router's entire native ETH balance to fund any WETH payment, regardless of which user deposited that ETH. An attacker can exploit ETH left on the router by a prior caller to execute a WETH swap at zero personal cost.

### Finding Description
`pay()` contains this logic for the WETH branch:

```solidity
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

`address(this).balance` is the router's global ETH balance, not the current caller's deposited amount. Any ETH stranded from a previous `payable` call (e.g., victim sent `{value: 1 ETH}` for a `0.5 ETH` swap and did not call `refundETH()`) is available to the next caller's `pay()` invocation. [3](#0-2) 

### Impact Explanation
Direct theft of user principal. The victim's stranded ETH is consumed to fund the attacker's swap output. The attacker receives real pool tokens without spending their own WETH. Loss equals `min(stranded_ETH, attacker_amountIn)`. This meets Critical/High thresholds under Sherlock rules.

### Likelihood Explanation
ETH stranding is a common user mistake with payable routers (sending excess ETH without a `refundETH()` call in the same multicall). The attacker needs no special permissions — only a public `exactInputSingle` call with `tokenIn=WETH`. The attack is atomic and requires no front-running beyond observing the router's ETH balance.

### Recommendation
Track per-caller deposited ETH (e.g., via transient storage keyed to `msg.sender`) and only allow `pay()` to use ETH that was deposited by the current `payer`. Alternatively, restrict the native-ETH path in `pay()` to only fire when `payer == address(this)` (i.e., mid-path), and require the external caller to have sent exactly the right `msg.value` in the same call frame, reverting otherwise.

### Proof of Concept
```solidity
// 1. Victim strands 0.5 ETH on router
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    tokenIn: WETH, amountIn: 0.5 ether, ...
}));
// Victim forgets refundETH(); router now holds 0.5 ETH

// 2. Attacker has 0 ETH, 0 WETH allowance
uint256 attackerWETHBefore = IERC20(WETH).balanceOf(attacker);
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    tokenIn: WETH, amountIn: 0.5 ether, ...recipient: attacker...
}));
// pay(WETH, attacker, pool, 0.5e18) fires:
//   nativeBalance = 0.5 ETH >= 0.5 ETH → wraps victim's ETH, sends WETH to pool
// Attacker receives pool output tokens

assert(IERC20(WETH).balanceOf(attacker) == attackerWETHBefore); // allowance untouched
assert(address(router).balance == 0);                            // victim's ETH gone
```

### Citations

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
