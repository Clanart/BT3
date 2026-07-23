Looking at the two key functions:

### Title
Unrestricted `sweepToken` Allows Any Caller to Drain All ERC20 Balances from the Router — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`sweepToken` is `public payable` with no access control. Any external caller can invoke it directly (or via `multicall`) to transfer the router's entire balance of any ERC20 token to an attacker-controlled address.

---

### Finding Description

`sweepToken` unconditionally transfers `IERC20(token).balanceOf(address(this))` to a caller-supplied `recipient` with no ownership check, no `msg.sender` restriction, and no transient-context guard: [1](#0-0) 

`multicall` is equally unrestricted — `public payable`, no modifier, delegate-calls arbitrary selectors on `address(this)`: [2](#0-1) 

The attack does not require `multicall` at all; `sweepToken` can be called directly. `multicall` merely allows batching it with other calls in one transaction.

---

### Impact Explanation

Any ERC20 balance held by the router — including USDC — can be stolen by an unprivileged caller. The router accumulates token balances in at least one documented code path: during `exactInput` multi-hop swaps, intermediate output tokens are sent to `address(this)` between hops: [3](#0-2) 

While these are consumed within the same transaction in the happy path, any tokens sent directly to the router (e.g., by users, integrators, or front-ends that pre-fund the router) are permanently exposed. Additionally, `unwrapWETH9` has the same issue for WETH/ETH balances. [4](#0-3) 

---

### Likelihood Explanation

The call requires zero privileges and zero setup. Any on-chain observer who sees a token balance on the router (via `eth_call` or mempool monitoring) can immediately drain it in a single transaction. The `amountMinimum` parameter can be set to `0`, bypassing even the dust-check guard.

---

### Recommendation

Restrict `sweepToken` and `unwrapWETH9` so they can only be called as part of a `multicall` initiated by the original `msg.sender`, or add an explicit `recipient == msg.sender` check, or gate both functions behind a `checkDeadline`/`checkCaller` modifier that validates the outer transaction initiator. A common pattern is to record `msg.sender` at `multicall` entry in transient storage and require `recipient == _originalCaller` inside sweep/unwrap helpers.

---

### Proof of Concept

```solidity
// Foundry fork test (mainnet USDC)
function test_sweepDrain() public {
    // Simulate router holding USDC (e.g., pre-funded by integrator)
    deal(USDC, address(router), 1_000e6);

    address attacker = makeAddr("attacker");
    vm.prank(attacker);
    // Direct call — no multicall needed
    router.sweepToken(USDC, 0, attacker);

    assertEq(IERC20(USDC).balanceOf(attacker), 1_000e6);
    assertEq(IERC20(USDC).balanceOf(address(router)), 0);
}
```

The same result is achieved via `multicall([abi.encodeCall(IPeripheryPayments.sweepToken, (USDC, 0, attacker))])`.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-106)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
```
