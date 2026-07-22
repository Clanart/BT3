The vulnerability is real. Let me trace the exact code path to confirm before writing the finding.

The `pay` function in `PeripheryPayments.sol` (lines 69–88) uses `address(this).balance` — the router's **total** ETH balance — without any attribution to the current caller: [1](#0-0) 

When `0 < nativeBalance < value`, it deposits the router's entire ETH balance as WETH, sends it to the pool, then pulls only `value - nativeBalance` from the payer's WETH allowance. The router's ETH balance includes ETH left by **prior callers** who sent excess `msg.value` and did not call `refundETH()`.

`exactInputSingle` is `payable` and does not enforce that `msg.value == amountIn`: [2](#0-1) 

The test `test_multicall_ethInput_exactInputSingle_refundsUnusedEth` confirms that excess ETH is left on the router when `refundETH()` is omitted: [3](#0-2) 

---

### Title
Prior-user ETH silently consumed by subsequent WETH swap — `PeripheryPayments.pay` partial-ETH branch — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.pay` uses `address(this).balance` (the router's total ETH balance) as the native contribution toward a WETH payment. Any ETH stranded on the router from a prior caller's excess `msg.value` is silently consumed by the next caller who swaps with `tokenIn = WETH`, reducing that caller's WETH pull by the stranded amount. The prior depositor loses their ETH without consent.

### Finding Description
`pay` contains three branches for `token == WETH`:

```solidity
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
} else if (nativeBalance > 0) {          // ← vulnerable branch
    IWETH9(WETH).deposit{value: nativeBalance}();
    IERC20(WETH).safeTransfer(recipient, nativeBalance);
    IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
} else {
    IERC20(WETH).safeTransferFrom(payer, recipient, value);
}
```

`address(this).balance` is the router's **global** ETH balance, not the ETH sent with the current call. ETH can be stranded on the router whenever a user calls a payable entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`) with `msg.value > amountIn` and omits `refundETH()` from their multicall. The `receive()` guard only blocks direct transfers; it does not prevent excess `msg.value` from accumulating.

Attack path:
1. Victim calls `multicall{value: 1 ether}([exactInputSingle(amountIn=0.3 ether)])` without `refundETH()`. The swap consumes 0.3 ETH; 0.7 ETH remains on the router.
2. Attacker calls `exactInputSingle(tokenIn=WETH, amountIn=1 ether)` with no `msg.value`.
3. Inside `_justPayCallback` → `pay(WETH, attacker, pool, 1 ether)`:
   - `nativeBalance = 0.7 ether` (victim's residual)
   - Middle branch fires: deposits 0.7 ETH as WETH → pool; pulls only 0.3 WETH from attacker's allowance.
4. Pool receives 1 WETH total. Attacker pays 0.3 WETH instead of 1 WETH. Victim loses 0.7 ETH. [4](#0-3) 

### Impact Explanation
Direct loss of user principal. Any ETH stranded on the router (a realistic user error, as the test suite itself demonstrates the pattern) is unconditionally transferred to the next WETH-input swap caller. The victim cannot recover the ETH once the attacker's transaction executes. Impact is **High**: unprivileged theft of another user's ETH with no protocol-level guard.

### Likelihood Explanation
**Medium.** The precondition — residual ETH on the router — requires a user to send excess `msg.value` without `refundETH()`. This is a documented and tested pattern (the test `test_multicall_ethInput_exactInputSingle_refundsUnusedEth` shows the correct multicall includes `refundETH()`; omitting it is a natural mistake). A MEV bot can monitor the mempool or router balance and front-run the victim's `refundETH()` call.

### Recommendation
Track only the ETH contributed by the **current call** (`msg.value`) rather than the router's total balance. Replace `address(this).balance` with a parameter or transient slot that records the ETH sent with the current top-level call:

```solidity
// In pay(), replace:
uint256 nativeBalance = address(this).balance;
// With:
uint256 nativeBalance = _getMsgValue(); // transient slot set at entry, cleared at exit
```

Alternatively, enforce `msg.value == 0 || msg.value == amountIn` at the entry points and reject calls where `msg.value` does not exactly match the intended native contribution.

### Proof of Concept
```solidity
// 1. Victim sends excess ETH, forgets refundETH
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    tokenIn: WETH, amountIn: 0.3 ether, ...
}));
// Router now holds 0.7 ETH

// 2. Attacker has approved router for 0.3 WETH only
weth.approve(address(router), 0.3 ether);

// 3. Attacker swaps 1 WETH worth — only 0.3 WETH pulled from allowance
router.exactInputSingle(ExactInputSingleParams({
    tokenIn: WETH, amountIn: 1 ether, ...
}));

// Pool received 1 WETH; attacker paid 0.3 WETH; victim lost 0.7 ETH
assert(address(router).balance == 0);
assert(weth.balanceOf(attacker) == initialWeth - 0.3 ether); // only 0.3 pulled
```

### Citations

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
