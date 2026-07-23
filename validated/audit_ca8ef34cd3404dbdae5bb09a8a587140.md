Audit Report

## Title
Unguarded `refundETH()` allows any caller to steal ETH stranded on the router by a prior user's excess `msg.value` — (File: metric-periphery/contracts/base/PeripheryPayments.sol)

## Summary
`refundETH()` transfers the router's entire ETH balance to `msg.sender` with no access control. When a user calls `multicall{value: X}` and the `pay()` function wraps only `amountIn` of that ETH into WETH, the surplus `X - amountIn` remains as raw ETH on the router. Any subsequent caller can invoke `refundETH()` in a separate transaction to drain that balance.

## Finding Description
`refundETH()` is `external payable` with no caller restriction and sends `address(this).balance` unconditionally to `msg.sender`: [1](#0-0) 

The `pay()` function, when `token == WETH` and `nativeBalance >= value`, wraps exactly `value` ETH and leaves `nativeBalance - value` as raw ETH on the contract: [2](#0-1) 

The `receive()` guard prevents direct ETH deposits from arbitrary addresses, so the only ETH source is `msg.value` from payable calls: [3](#0-2) 

If a user calls `multicall{value: 2 ether}` with `amountIn = 1000 wei` and omits `refundETH()` as the final call, `2 ether - 1000 wei` persists on the router after the transaction ends. The test suite confirms `refundETH()` is the only recovery mechanism and must be explicitly included: [4](#0-3) 

## Impact Explanation
Direct loss of user ETH principal. An attacker monitoring the mempool or chain for multicall transactions that leave a non-zero ETH balance on the router can call `refundETH()` in the next block and receive the full stranded balance. No privileged access is required.

## Likelihood Explanation
Requires user error: the user must send excess `msg.value` in a multicall without appending `refundETH()`. The `receive()` guard prevents the attacker from manufacturing the precondition. The pattern is common in production integrations (e.g., frontend-generated calldata, slippage buffers), making omissions plausible. Severity is **Medium** — direct fund loss conditioned on user error, consistent with Sherlock's treatment of the identical Uniswap v3 periphery pattern.

## Recommendation
Track per-caller ETH deposits using transient storage (EIP-1153) mapping `msg.sender → deposited` and restrict `refundETH()` to return only the caller's own deposited amount. Alternatively, enforce that `refundETH()` is only callable within the same multicall transaction that deposited the ETH (e.g., via a transient reentrancy flag set at multicall entry).

## Proof of Concept
```solidity
// Victim sends 2 ether but only needs 1000 wei for the swap, forgets refundETH()
vm.prank(victim);
bytes[] memory calls = new bytes[](1);
calls[0] = abi.encodeWithSelector(router.exactInputSingle.selector, params); // amountIn=1000
router.multicall{value: 2 ether}(calls);
// 2 ether - 1000 wei is now stranded on the router

// Attacker drains it in a subsequent transaction
uint256 attackerBefore = attacker.balance;
vm.prank(attacker);
router.refundETH();
assertGt(attacker.balance, attackerBefore);
assertEq(address(router).balance, 0);
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-77)
```text
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
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
