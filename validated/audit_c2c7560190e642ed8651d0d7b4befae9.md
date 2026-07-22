The code path is real and the vulnerability is valid. Here is the full analysis:

---

### Title
Stranded ETH in Router Subsidizes Attacker's WETH Swap, Stealing Prior User's ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

The partial-ETH branch in `pay()` uses any ETH already held by the router to partially fund a WETH swap, pulling only the remainder from the caller via `transferFrom`. Because ETH can be legitimately stranded in the router between transactions (e.g., a user sends `msg.value` with a non-WETH swap, or sends excess ETH), an attacker can call `exactInputSingle(tokenIn=WETH)` with `msg.value=0` and have the pool receive the full `amountIn` while the attacker is only charged `amountIn - strandedETH`.

### Finding Description

`pay()` contains three branches for WETH payments: [1](#0-0) 

The middle branch (`nativeBalance > 0 && nativeBalance < value`) wraps the router's entire native balance into WETH, transfers it to the pool, then pulls only `value - nativeBalance` from the payer: [2](#0-1) 

ETH is stranded in the router whenever any user sends `msg.value` with a payable function for a non-WETH swap (the ETH is never consumed), or sends excess ETH with a WETH swap and does not call `refundETH()`. The `receive()` guard only blocks direct plain-ETH transfers; it does **not** block `msg.value` attached to function calls: [3](#0-2) 

`exactInputSingle` is `payable` and has no check that `msg.value` is zero when `tokenIn != WETH`, so any caller can accidentally leave ETH behind: [4](#0-3) 

The callback path that reaches `pay()` is:

`exactInputSingle` → pool `swap()` → `metricOmmSwapCallback` → `_justPayCallback` → `pay()` [5](#0-4) 

### Impact Explanation

**Direct theft of stranded ETH.** If the router holds `S` ETH from a prior user and an attacker calls `exactInputSingle(tokenIn=WETH, amountIn=V)` with `msg.value=0`:

- Pool receives `V` WETH (full amount).
- Attacker is charged only `V - S` WETH via `transferFrom`.
- The prior user's `S` ETH is permanently consumed.

The prior user loses their stranded ETH with no recourse. The attacker gains a discount equal to `S` on their swap. This is a direct loss of user principal, satisfying the High impact gate.

### Likelihood Explanation

ETH stranding is a realistic, non-exotic precondition:

1. A user calls `exactInput` or `exactInputSingle` with `msg.value > 0` when `tokenIn` is not WETH — the ETH is never used and is left in the router.
2. A user sends excess ETH for a WETH swap and omits `refundETH()` from their multicall.
3. Any integrator that does not carefully construct multicalls can trigger this.

No privileged role, malicious pool, or non-standard token is required.

### Recommendation

Two complementary fixes:

1. **In `pay()`**: when `token == WETH` and `payer != address(this)`, only use `address(this).balance` if it was explicitly sent in the current call (i.e., compare against `msg.value`). Replace `address(this).balance` with `msg.value` as the ETH budget, or require `nativeBalance == msg.value`.

2. **In `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`**: revert if `msg.value > 0 && tokenIn != WETH` (analogous to Uniswap V3's `checkInputTokenIsWETH9` guard), preventing ETH from being stranded in the first place.

### Proof of Concept

```solidity
// Foundry test sketch
function test_strandedEthStealsFromPriorUser() public {
    // Prior user accidentally sends 30 ETH with a non-WETH swap
    vm.deal(priorUser, 30 ether);
    vm.prank(priorUser);
    // exactInputSingle with tokenIn=USDC, msg.value=30 ETH → ETH stranded
    router.exactInputSingle{value: 30 ether}(usdcSwapParams);
    assertEq(address(router).balance, 30 ether);

    // Attacker swaps WETH with msg.value=0, amountIn=100 WETH
    vm.prank(attacker);
    weth.approve(address(router), 70 ether); // only 70, not 100
    router.exactInputSingle(wethSwapParams); // amountIn=100 WETH

    // Pool received 100 WETH; attacker paid only 70 WETH
    assertEq(weth.balanceOf(pool), initialPoolBalance + 100 ether);
    assertEq(weth.balanceOf(attacker), attackerInitialBalance - 70 ether);
    // Prior user's 30 ETH is gone
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
