The `sweepToken` function is confirmed. Let me verify there are no access controls or callback context checks anywhere in the inheritance chain.

The code confirms the vulnerability. Here is the full assessment:

---

### Title
Unrestricted `sweepToken` Allows Any Caller to Drain Router ERC20 Balance to Arbitrary Address — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.sweepToken` is `public payable` with no access control and an attacker-controlled `recipient` parameter. Any EOA can call it with `amountMinimum = 0` and drain the router's entire balance of any ERC20 token to an arbitrary address.

### Finding Description
`sweepToken` at line 48 of `PeripheryPayments.sol` performs no check on `msg.sender` and no check that `recipient == msg.sender`. The only guard is the `amountMinimum` floor, which is trivially bypassed by passing `0`. [1](#0-0) 

Compare with `refundETH` (line 58–63), which correctly hard-codes `msg.sender` as the destination — the asymmetry is the defect. [2](#0-1) 

The router's `pay` internal function has a branch `if (payer == address(this))` that transfers from the router's own ERC20 balance during multi-hop exact-output callback chains. While that path is intra-transaction, the router can hold a non-zero ERC20 balance in several realistic situations:

- A user stages tokens into the router via a `multicall` step (e.g., a `selfPermit` + direct transfer step) before a swap step; an attacker can front-run the second step.
- Tokens are accidentally sent directly to the router (a common user error with routers).
- Any future integration that pre-funds the router before calling a swap. [3](#0-2) 

The `multicall` implementation uses `Address.functionDelegateCall`, which propagates reverts atomically, so a failed intermediate step does not strand tokens mid-multicall. The PoC's "failed multicall step" framing is therefore incorrect. However, the front-running scenario against a staged multicall is valid. [4](#0-3) 

### Impact Explanation
Any ERC20 balance held by the router — regardless of how it arrived — can be fully drained to an attacker-controlled address in a single call. The impact is direct, complete loss of the affected token balance with no recovery path.

### Likelihood Explanation
The call requires no special role, no pool interaction, and no prior state. The only precondition is that the router holds a non-zero ERC20 balance, which is achievable via direct transfer or front-running a staged multicall. Likelihood is **Medium** (precondition required) with **High** impact, placing overall severity at **High**.

### Recommendation
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

### Proof of Concept
```solidity
// 1. Deploy router (weth, factory)
// 2. Transfer 1000 USDC directly to router address
assertEq(USDC.balanceOf(address(router)), 1000);

// 3. Attacker (fresh EOA, no approvals, no role) calls:
vm.prank(attacker);
router.sweepToken(address(USDC), 0, attacker);

// 4. Assert
assertEq(USDC.balanceOf(attacker), 1000);   // attacker received all USDC
assertEq(USDC.balanceOf(address(router)), 0); // router drained
```

This matches the existing test pattern in `MetricOmmSimpleRouterPaymentsTest.test_sweepToken_sendsFullBalance`, which already demonstrates the unrestricted drain — the only difference is the caller is not the depositor. [5](#0-4)

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L57-63)
```text
  /// @inheritdoc IPeripheryPayments
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
