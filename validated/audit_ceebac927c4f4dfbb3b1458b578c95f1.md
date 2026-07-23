Audit Report

## Title
Trapped ETH in `MetricOmmPoolLiquidityAdder` is silently consumed to subsidize a subsequent user's WETH liquidity deposit — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

The `pay()` function's WETH branch reads `address(this).balance` — the entire contract ETH balance — rather than only ETH the current caller contributed. Any ETH left in the contract by a prior user (via a payable entry point without a subsequent `refundETH()`) is silently wrapped and transferred to the pool on behalf of the next WETH depositor, causing the prior user to permanently lose their ETH while the next user receives a proportional discount.

## Finding Description

**ETH accumulation.** The `receive()` guard at [1](#0-0)  only blocks bare ETH transfers (no calldata). ETH sent as `msg.value` to any `payable` entry point bypasses it entirely. All of the following are `payable`: `multicall` [2](#0-1) , `addLiquidityExactShares` [3](#0-2) , `addLiquidityWeighted` [4](#0-3) , `refundETH`, `unwrapWETH9`, and `sweepToken`. [5](#0-4)  A user who calls `multicall{value: X}([addLiquidityExactShares(...)])` for a non-WETH pool and omits `refundETH()` leaves `X` wei permanently in the contract.

**WETH branch consumes full contract balance.** In `pay()`, the WETH branch unconditionally reads `address(this).balance` — the entire contract balance, not just ETH the current caller sent: [6](#0-5)  When `nativeBalance > 0` from a prior user's trapped ETH, the contract wraps that ETH and transfers it to the pool, then pulls only `value - nativeBalance` from the current payer via `safeTransferFrom`. The prior user's ETH is gone; the current user received a discount equal to `nativeBalance`.

**Callback confirms payer is always `msg.sender` of the outer call.** The payer stored in transient context is set to `msg.sender` at `_addLiquidity` time: [7](#0-6)  and loaded in the callback: [8](#0-7)  There is no mechanism restricting `address(this).balance` to only ETH the current payer sent.

## Impact Explanation

Direct, irreversible loss of user principal (ETH). User A's ETH is permanently transferred to the pool on behalf of User B, with no recovery path. This matches the "Critical/High direct loss of user principal" impact gate. The magnitude equals the full trapped ETH balance, which can be any amount a user sent to a payable entry point.

## Likelihood Explanation

The multicall + omitted `refundETH()` pattern is a well-documented footgun in Uniswap-style routers; front-ends, scripts, and integrators routinely omit the refund step. The attacker (User B) requires no special role — only a normal, permissionless `addLiquidityExactShares` call to any WETH pool. The contract's ETH balance is publicly readable on-chain, making the attack trivially detectable and repeatable by any observer.

## Recommendation

Restrict the WETH branch in `pay()` to only ETH the current caller explicitly sent in the current transaction. The standard fix is to track `msg.value` at the outer entry point and pass it through to `pay()` as the maximum native contribution, using only that amount rather than `address(this).balance`. Alternatively, enforce `address(this).balance == msg.value` at the start of each non-multicall entry point, or automatically refund any remaining ETH balance to the payer after each `pay()` invocation.

## Proof of Concept

```solidity
// Foundry test sketch
function test_trappedEthSubsidizesWethDeposit() public {
    // User A sends ETH via multicall for a non-WETH pool, forgets refundETH()
    vm.deal(userA, 1 ether);
    vm.prank(userA);
    adder.multicall{value: 1 ether}(
        _encodeAddLiquidityForNonWethPool(...)
    );
    // 1 ETH is now trapped in the adder
    assertEq(address(adder).balance, 1 ether);

    // User B adds liquidity to a WETH pool requiring 2 WETH
    uint256 wethBefore = weth.balanceOf(userB);
    vm.prank(userB);
    adder.addLiquidityExactShares(wethPool, userB, salt, deltas, 2e18, 0, "");
    uint256 wethAfter = weth.balanceOf(userB);

    // User B only spent 1 WETH (2 - 1 trapped ETH), pool received full 2 WETH
    assertEq(wethBefore - wethAfter, 1 ether); // should be 2 ether — FAIL
    assertEq(address(adder).balance, 0);        // user A's ETH is gone
}
```

The pool receives the full `value` in WETH, User B's `safeTransferFrom` is reduced by `nativeBalance`, and User A's ETH is permanently lost with no revert or event indicating the misappropriation.

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L37-63)
```text
  function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);

    if (balanceWETH > 0) {
      IWETH9(WETH).withdraw(balanceWETH);
      _transferETH(recipient, balanceWETH);
    }
  }

  /// @inheritdoc IPeripheryPayments
  function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);

    if (balanceToken > 0) {
      IERC20(token).safeTransfer(recipient, balanceToken);
    }
  }

  /// @inheritdoc IPeripheryPayments
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L42-42)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-64)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L67-67)
```text
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L88-100)
```text
  function addLiquidityWeighted(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata weightDeltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    int8 minimalCurBin,
    uint104 minimalPosition,
    int8 maximalCurBin,
    uint104 maximalPosition,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-177)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }

    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```
