Audit Report

## Title
Unrestricted `sweepToken` Allows Any Caller to Drain Router ERC20 Balance to Arbitrary Address — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.sweepToken` is `public payable` with no `msg.sender` check and an attacker-controlled `recipient` parameter. Any EOA can call it with `amountMinimum = 0` and drain the router's entire balance of any ERC20 token to an arbitrary address. The router is designed to hold intermediate ERC20 balances during multi-hop exact-input swaps and can also receive tokens via direct transfer.

## Finding Description
`sweepToken` at line 48 of `PeripheryPayments.sol` performs no check on `msg.sender` and no check that `recipient == msg.sender`. The only guard is the `amountMinimum` floor, which is trivially bypassed by passing `0`. [1](#0-0) 

`refundETH` (lines 58–63) correctly hard-codes `msg.sender` as the destination, making the asymmetry clear — `sweepToken` and `unwrapWETH9` both accept an arbitrary `recipient` with no caller restriction. [2](#0-1) 

The `pay` internal function's `payer == address(this)` branch confirms the router is designed to hold ERC20 balances during multi-hop exact-input swaps, where intermediate tokens are routed through `address(this)` between hops. [3](#0-2) 

In `exactInput`, intermediate hop outputs are explicitly sent to `address(this)`: [4](#0-3) 

While intermediate tokens are consumed atomically within the same transaction's loop, the router can hold a non-zero ERC20 balance via direct transfer (a common user error with routers). No access control in the inheritance chain (`MetricOmmSwapRouterBase`, `SelfPermit`) adds any restriction to `sweepToken`.

The `multicall` front-running scenario is not valid — `Address.functionDelegateCall` propagates reverts atomically, so a failed intermediate step cannot strand tokens mid-multicall. [5](#0-4) 

## Impact Explanation
Any ERC20 balance held by the router — regardless of how it arrived — can be fully drained to an attacker-controlled address in a single call with no special role, no pool interaction, and no prior state beyond the router holding a non-zero balance. This constitutes direct, complete loss of the affected token balance with no recovery path, meeting the High impact threshold.

## Likelihood Explanation
The call requires no special role and no pool interaction. The only precondition is that the router holds a non-zero ERC20 balance, which is achievable via direct transfer (a realistic and common user error with routers). Likelihood is **Medium** (precondition required), impact is **High** (complete drain of affected balance), placing overall severity at **High**.

## Recommendation
Restrict `sweepToken` (and `unwrapWETH9`) so that `recipient` must equal `msg.sender`, mirroring the `refundETH` pattern:

```solidity
function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
+   if (recipient != msg.sender) revert RecipientNotSender();
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);
    if (balanceToken > 0) {
        IERC20(token).safeTransfer(recipient, balanceToken);
    }
}
```

Alternatively, remove the `recipient` parameter entirely and always send to `msg.sender`.

## Proof of Concept
The existing test `test_sweepToken_sendsFullBalance` already demonstrates the unrestricted drain — the only difference from the attack scenario is that the caller is not the depositor: [6](#0-5) 

Minimal PoC:
```solidity
// 1. Transfer 1000 USDC directly to router address (user error)
token1.mint(address(router), 1000);
assertEq(token1.balanceOf(address(router)), 1000);

// 2. Attacker (fresh EOA, no approvals, no role) calls:
vm.prank(attacker);
router.sweepToken(address(token1), 0, attacker);

// 3. Assert
assertEq(token1.balanceOf(attacker), 1000);   // attacker received all tokens
assertEq(token1.balanceOf(address(router)), 0); // router drained
```

### Citations

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L69-72)
```text
  function pay(address token, address payer, address recipient, uint256 value) internal {
    // If the payer is contract it means we are in the middle of a path. In the middle of a path we operate on ERC20 only.
    if (payer == address(this)) {
      IERC20(token).safeTransfer(recipient, value);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-107)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
```

**File:** metric-periphery/test/MetricOmmSimpleRouter.payments.t.sol (L57-67)
```text
  function test_sweepToken_sendsFullBalance() public {
    uint256 amount = 123_456;
    token1.mint(address(router), amount);

    uint256 recipientBefore = token1.balanceOf(recipient);

    router.sweepToken(address(token1), amount, recipient);

    assertEq(token1.balanceOf(address(router)), 0, "router token cleared");
    assertEq(token1.balanceOf(recipient) - recipientBefore, amount, "recipient token");
  }
```
