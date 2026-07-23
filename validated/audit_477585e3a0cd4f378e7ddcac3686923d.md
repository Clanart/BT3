The `pay` function's WETH branch is the critical code to analyze. Let me trace the full path precisely.

### Title
Stranded ETH in Router Consumed by Subsequent WETH Swaps — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay` function's WETH branch unconditionally uses `address(this).balance` to fund any WETH swap, with no ownership tracking. ETH left in the router from a prior `multicall{value}` call (where the user omitted `refundETH()`) is silently consumed by the next caller's WETH swap, causing direct loss of the original depositor's ETH.

---

### Finding Description

`pay` in `PeripheryPayments.sol` implements this logic for WETH payments: [1](#0-0) 

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);   // payer's WETH never touched
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

When `nativeBalance >= value`, the router wraps its own ETH and transfers WETH to the pool — the designated `payer`'s WETH allowance is never touched. There is no check that the ETH in the router belongs to the current caller.

ETH enters the router legitimately via `multicall{value}` (a `payable` function): [2](#0-1) 

The `receive()` guard only blocks direct ETH sends from non-WETH addresses; it does not prevent ETH from accumulating via `multicall{value}`: [3](#0-2) 

The protocol's own test documents the correct pattern — always include `refundETH()` in the same multicall when sending excess ETH: [4](#0-3) 

If a user omits `refundETH()`, the excess ETH persists in the router across transaction boundaries. The next caller who invokes any WETH swap (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`) will have their payment silently sourced from the stranded ETH rather than from their own WETH allowance.

---

### Impact Explanation

**Direct loss of user principal.** The original ETH depositor loses up to `address(this).balance` ETH per subsequent WETH swap. The beneficiary caller pays nothing from their own WETH balance. The pool receives correctly wrapped WETH, so pool accounting is unaffected — the loss is entirely borne by the victim who left ETH in the router.

---

### Likelihood Explanation

The `multicall{value}` + `exactInput*(WETH)` pattern is the documented ETH-input flow. Users who send slightly more ETH than needed (e.g., to avoid slippage-induced reverts) and omit `refundETH()` — a common mistake — leave ETH stranded. Any subsequent WETH swap in the same block or later drains it. No attacker setup is required; the beneficiary simply calls the router normally.

---

### Recommendation

Track per-call ETH attribution. The simplest fix is to record `msg.value` at the start of each swap entry point and limit the WETH branch to consuming at most that amount, reverting or ignoring any pre-existing balance. Alternatively, enforce that `address(this).balance == 0` at the start of every swap entry point (after accounting for `msg.value`), or restrict the ETH-wrapping path to only the ETH sent in the current call (`msg.value`).

---

### Proof of Concept

```solidity
// 1. Victim sends 1 ETH via multicall but forgets refundETH()
vm.prank(victim);
bytes[] memory calls = new bytes[](1);
calls[0] = abi.encodeCall(router.exactInputSingle, (ExactInputSingleParams({
    pool: pool, tokenIn: WETH, tokenOut: token1,
    zeroForOne: true, amountIn: 0.5 ether, amountOutMinimum: 0,
    recipient: victim, deadline: block.timestamp + 1,
    priceLimitX64: 0, extensionData: ""
})));
router.multicall{value: 1 ether}(calls);
// 0.5 ETH remains in router

// 2. Attacker calls exactInputSingle for WETH — no ETH sent, no WETH approval needed
uint256 attackerWethBefore = IERC20(WETH).balanceOf(attacker);
vm.prank(attacker);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool, tokenIn: WETH, tokenOut: token1,
    zeroForOne: true, amountIn: 0.3 ether, amountOutMinimum: 0,
    recipient: attacker, deadline: block.timestamp + 1,
    priceLimitX64: 0, extensionData: ""
}));

// Assert: attacker's WETH was NOT consumed; victim's ETH was
assertEq(IERC20(WETH).balanceOf(attacker), attackerWethBefore, "attacker WETH unchanged");
assertEq(address(router).balance, 0.2 ether, "0.3 ETH drained from router");
```

The pool receives valid WETH (wrapped from the victim's stranded ETH), the swap succeeds, and the attacker's WETH balance is untouched. The victim's 0.3 ETH is permanently lost.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
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
