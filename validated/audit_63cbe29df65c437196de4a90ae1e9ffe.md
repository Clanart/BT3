Looking at the actual code to validate the claim:

The code is confirmed. Let me assess the validity of this claim.

**Key findings from the actual code:**

1. `sweepToken` and `unwrapWETH9` are `public payable` with no `msg.sender` guard and accept a caller-supplied `recipient` — confirmed at L37-55 of `PeripheryPayments.sol`.

2. `refundETH` sends the full ETH balance to `msg.sender` with no access control — confirmed at L58-63.

3. The test file `MetricOmmSimpleRouter.native.t.sol` explicitly documents the intended pattern at L8-10: *"ETH output: swap WETH to router, then unwrapWETH9 in the **same multicall**"* — meaning the design requires atomic bundling, but the functions are independently callable.

4. The test at L135-162 shows the intended two-step pattern: `exactInputSingle` with `recipient = address(router)`, then `unwrapWETH9` — both inside a single `multicall`. If a user splits these into two transactions, WETH is stranded on the router and stealable.

5. The `pay()` function at L73-84 confirms excess native ETH stays on the router when `nativeBalance > value` for WETH swaps, and `refundETH` sends it to whoever calls it.

The claim's code citations are accurate, the attack path is reachable by any unprivileged caller, and the impact is direct loss of user principal. This is a valid finding.

---

Audit Report

## Title
Unrestricted `sweepToken`, `unwrapWETH9`, and `refundETH` Allow Any Caller to Drain Router-Held Tokens — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`sweepToken`, `unwrapWETH9`, and `refundETH` in `PeripheryPayments.sol` are public/external with no `msg.sender` guard and no per-user balance accounting. They drain the router's entire balance of any token, WETH, or native ETH to a caller-chosen address. Because the intended ETH-output flow requires routing WETH through `address(router)` before unwrapping, any user who does not bundle the swap and unwrap atomically in a single `multicall` leaves funds stealable by any front-running attacker.

## Finding Description
`sweepToken` (L48-55) and `unwrapWETH9` (L37-45) are declared `public payable` with no access control:

```solidity
// PeripheryPayments.sol L37-45
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);
    if (balanceWETH > 0) {
        IWETH9(WETH).withdraw(balanceWETH);
        _transferETH(recipient, balanceWETH);
    }
}

// PeripheryPayments.sol L48-55
function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);
    if (balanceToken > 0) {
        IERC20(token).safeTransfer(recipient, balanceToken);
    }
}
```

Both functions transfer the router's **full** balance to a caller-supplied `recipient` with `amountMinimum = 0` accepted. There is no check that `msg.sender` is the depositor, no per-user balance ledger, and no restriction on `recipient`.

The documented ETH-output flow (confirmed in `MetricOmmSimpleRouter.native.t.sol` L8-10 and L135-162) requires the user to set `recipient = address(router)` in the swap, then call `unwrapWETH9` — ideally in the same `multicall`. If a user splits these into two separate transactions, WETH lands on the router between them and is immediately stealable.

`refundETH` (L58-63) sends the entire native ETH balance to `msg.sender`:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
```

The `pay()` helper (L73-84) deposits exactly `value` of native ETH as WETH when `nativeBalance >= value`, leaving any excess `nativeBalance - value` on the router. If the user does not call `refundETH` in the same `multicall`, any caller can claim that ETH.

Existing guards are insufficient: the only check is `amountMinimum`, which the attacker sets to `0`. There is no callback-context guard, no ownership check, and no transient-storage attribution tying a balance to its depositor.

## Impact Explanation
Direct loss of user principal. Any attacker can call `sweepToken(token, 0, attacker)` or `unwrapWETH9(0, attacker)` to drain the router's entire balance of any ERC-20 or WETH to an arbitrary address. Similarly, `refundETH()` sends the full native ETH balance to `msg.sender`, so any caller can claim ETH belonging to a different user. This is a High-severity direct theft of user funds with no privileged precondition, matching the allowed impact gate for direct loss of user principal.

## Likelihood Explanation
Medium-High. Two realistic stranding paths require no privileged access:

1. **WETH-output swap with `recipient = address(router)` in a separate transaction**: A user calls `exactInputSingle` or `exactInput` with WETH as the output token and sets `recipient = address(router)`, intending to call `unwrapWETH9` in a subsequent transaction rather than a `multicall`. WETH lands on the router. An attacker front-runs the `unwrapWETH9` call with `unwrapWETH9(0, attacker)`.

2. **Excess ETH from a WETH-input swap**: A user calls `exactInputSingle` with WETH as `tokenIn` and sends `msg.value > amountIn`. The `pay()` function deposits exactly `amountIn` as WETH; the residual `msg.value - amountIn` stays on the router as native ETH. If the user does not include `refundETH()` in the same `multicall`, an attacker calls `refundETH()` and receives the ETH.

Both paths are reachable by any external caller and are repeatable.

## Recommendation
Remove the caller-supplied `recipient` parameter and bind the recipient to `msg.sender`, mirroring the minimal fix:

```solidity
function sweepToken(address token, uint256 amountMinimum) public payable {
    address recipient = msg.sender;
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);
    if (balanceToken > 0) IERC20(token).safeTransfer(recipient, balanceToken);
}

function unwrapWETH9(uint256 amountMinimum) public payable {
    address recipient = msg.sender;
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);
    if (balanceWETH > 0) { IWETH9(WETH).withdraw(balanceWETH); _transferETH(recipient, balanceWETH); }
}
```

This ensures that only the caller who initiates the sweep or unwrap receives the funds. When used inside `multicall`, `msg.sender` is preserved via `delegatecall`, so the original user remains the recipient. Alternatively, maintain a per-user transient balance ledger so only the depositor can sweep their own residue.

## Proof of Concept
```solidity
// Attack scenario: steal WETH stranded by a user's two-transaction flow

// Tx 1 — Victim
router.exactInputSingle(ExactInputSingleParams({
    pool:             WETH_TOKEN1_POOL,
    tokenIn:          TOKEN1,
    tokenOut:         WETH,
    recipient:        address(router),   // intends to unwrap in next tx
    amountIn:         3_000,
    amountOutMinimum: 0,
    zeroForOne:       false,
    priceLimitX64:    type(uint128).max,
    deadline:         block.timestamp + 60,
    extensionData:    ""
}));
// WETH now sits on the router; victim has not yet called unwrapWETH9

// Tx 2 — Attacker (front-runs victim's unwrapWETH9 call)
router.unwrapWETH9(0, attacker);
// Attacker receives all WETH as ETH; victim receives nothing

// Foundry test skeleton:
// 1. Deploy router and pool as in SimpleRouterTestBase
// 2. Prank victim: call exactInputSingle with recipient=address(router), tokenOut=WETH
// 3. Assert weth.balanceOf(address(router)) > 0
// 4. Prank attacker: call router.unwrapWETH9(0, attacker)
// 5. Assert attacker received ETH and router WETH balance is 0
// 6. Assert victim received nothing
```

The same pattern applies to `sweepToken` for any ERC-20 and to `refundETH` for excess native ETH left from a WETH-input swap where `msg.value > amountIn`.