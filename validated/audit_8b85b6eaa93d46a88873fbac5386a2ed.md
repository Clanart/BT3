Audit Report

## Title
Unrestricted `sweepToken` and `unwrapWETH9` Allow Anyone to Drain Router Token Balances to an Arbitrary Recipient - (File: metric-periphery/contracts/base/PeripheryPayments.sol)

## Summary
`sweepToken` and `unwrapWETH9` in `PeripheryPayments.sol` are `public payable` with no `msg.sender` restriction and accept a fully attacker-controlled `recipient` address. Any tokens held by the router — including WETH routed there for a WETH→ETH unwrap — can be redirected to an attacker's address by anyone who calls these functions before the legitimate user does, resulting in complete loss of the user's swap output.

## Finding Description
Both functions transfer the router's entire balance to a caller-supplied `recipient` with no access control:

`unwrapWETH9` ( [1](#0-0) ) reads `IERC20(WETH).balanceOf(address(this))` and sends the full balance to `recipient` with no check on `msg.sender`.

`sweepToken` ( [2](#0-1) ) does the same for any ERC-20 token.

The intended atomic usage pattern is to batch a swap with `recipient: address(router)` followed by `unwrapWETH9`/`sweepToken` inside a single `multicall` call. The `multicall` implementation uses `delegatecall` and executes atomically ( [3](#0-2) ), but this atomicity is never enforced — both sweep/unwrap functions are independently callable as standalone transactions.

In `exactInput`, intermediate hop outputs are explicitly routed to `address(this)` ( [4](#0-3) ), and the final output can also be directed to the router when the user intends to follow up with `unwrapWETH9`. If the follow-up call is not in the same `multicall`, the balance is exposed in the mempool window between the two transactions.

No existing guard restricts who may call these functions or who may be named as `recipient`. The `receive()` guard only restricts ETH deposits to come from WETH ( [5](#0-4) ) and is unrelated to the sweep/unwrap access control gap.

## Impact Explanation
A front-running attacker can steal the entire output of any swap that routes through the router for WETH unwrapping or token sweeping performed in a separate transaction. This is a direct, complete loss of user principal with no recovery path. The attacker requires zero capital and zero permissions, satisfying the Critical/High direct loss of user principal threshold.

## Likelihood Explanation
The `multicall` batching requirement is not enforced at the contract level and is absent from function NatSpec. Any user, script, wallet, or integration that calls `unwrapWETH9` or `sweepToken` as a standalone transaction after routing swap output to the router is fully exposed. A mempool observer can execute the attack trivially and repeatably on every such transaction.

## Recommendation
Restrict `recipient` to `msg.sender` in both functions:

```solidity
function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    require(recipient == msg.sender, "recipient must be caller");
    ...
}

function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    require(recipient == msg.sender, "recipient must be caller");
    ...
}
```

Alternatively, make both functions `internal` and expose them only through `multicall`, structurally enforcing atomicity rather than relying on caller discipline.

## Proof of Concept
1. User calls `exactInputSingle(..., tokenOut: WETH, recipient: address(router))` — WETH lands in the router.
2. Attacker observes the pending transaction in the mempool and immediately submits `router.unwrapWETH9(0, attacker)` with higher gas.
3. Attacker's call executes first; the router's full WETH balance is unwrapped and sent to the attacker as ETH.
4. User's follow-up `unwrapWETH9(0, user)` finds zero balance and receives nothing (or reverts if `amountMinimum > 0`).

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

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
