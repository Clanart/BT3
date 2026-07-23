### Title
Stranded ETH from payable swap/liquidity functions is stealable by any caller via unauthenticated `refundETH()` — (File: metric-periphery/contracts/base/PeripheryPayments.sol)

---

### Summary

Every payable entry point on `MetricOmmSimpleRouter` and `MetricOmmPoolLiquidityAdder` (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`, `addLiquidityExactShares`, `addLiquidityWeighted`) can leave a native-ETH residue on the router after execution. The `refundETH()` helper that is meant to recover that residue has **no access control**: it transfers the router's entire ETH balance to whoever calls it. Any third party who calls `refundETH()` after a victim's transaction claims the victim's stranded ETH.

---

### Finding Description

**`pay()` consumes only the exact swap/liquidity amount, leaving the rest on the router.** [1](#0-0) 

```solidity
} else if (token == WETH) {
  uint256 nativeBalance = address(this).balance;
  if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();   // deposits exactly `value`, not nativeBalance
    IERC20(WETH).safeTransfer(recipient, value);
  }
```

`value` is the actual swap/liquidity amount delivered by the pool callback. If `msg.value > value` (e.g., a user sends `amountInMaximum` for an exact-output swap but the pool only needs a fraction of it), the difference `nativeBalance − value` is silently left on the router.

**`refundETH()` is unauthenticated and sweeps the full balance to `msg.sender`.** [2](#0-1) 

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);   // no check that msg.sender is the original payer
    }
  }
```

There is no mapping of depositor → amount, no `msg.sender` check, and no per-transaction attribution. Any EOA or contract that calls `refundETH()` after the victim's transaction receives the entire stranded balance.

The design intent documented in the test suite is that callers compose `multicall{value}([swap, refundETH()])` so the refund happens atomically in the same transaction: [3](#0-2) 

However, every payable entry point is individually callable without multicall. A user who calls `exactOutputSingle{value: X}` directly — a natural pattern when the caller does not know the exact input in advance — will strand `X − amountIn` ETH on the router with no automatic recovery.

---

### Impact Explanation

**Direct loss of user-principal ETH.** The stranded amount equals `msg.value − actual_amountIn`, which for exact-output swaps can be a large fraction of `amountInMaximum`. The attacker's cost is a single cheap `refundETH()` call; no special privilege is required. This satisfies the Critical/High direct-loss-of-user-principal gate.

---

### Likelihood Explanation

**Medium.** The exact-output swap pattern (`exactOutputSingle`, `exactOutput`) is the primary case where a user legitimately cannot know the exact input in advance and will naturally send a conservative `msg.value`. Integrators that wrap the router without multicall (e.g., aggregators, smart-contract wallets, scripts) are the most likely victims. A front-running bot watching the mempool can reliably extract the residue in the very next block.

---

### Recommendation

Add an unconditional `refundETH()` call at the end of every payable entry point that may leave a native-ETH residue, mirroring the fix applied in the referenced upstream commit. Alternatively, record the payer address in transient storage at entry and enforce `msg.sender == payer` inside `refundETH()`.

```solidity
function exactOutputSingle(ExactOutputSingleParams calldata params)
    external payable checkDeadline(params.deadline)
    returns (uint256 amountIn)
{
    // ... existing swap logic ...
    refundETH();   // add this
}
```

The same fix applies to `exactOutput`, `addLiquidityExactShares`, and `addLiquidityWeighted`.

---

### Proof of Concept

1. Alice calls `router.exactOutputSingle{value: 2 ether}(params)` where `params.amountOut = 1_000` tokens and `params.amountInMaximum = 2 ether`. The pool fills the order for `1 ether` of WETH input.
2. Inside `metricOmmSwapCallback`, `pay()` is called with `value = 1 ether`. Because `address(this).balance (2 ether) >= value (1 ether)`, it deposits exactly `1 ether` as WETH and transfers it to the pool. The remaining `1 ether` stays on the router.
3. Alice's transaction completes. She received her tokens but `1 ether` is stranded on the router with no automatic refund.
4. Bob calls `router.refundETH()` in the next transaction. `refundETH()` reads `address(this).balance == 1 ether` and calls `_transferETH(msg.sender, 1 ether)`, sending Bob `1 ether`.
5. Alice has permanently lost `1 ether`. [2](#0-1) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
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
