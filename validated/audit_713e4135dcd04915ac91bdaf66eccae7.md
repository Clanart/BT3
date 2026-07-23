Audit Report

## Title
Unrestricted `sweepToken`, `unwrapWETH9`, and `refundETH` Allow Any Caller to Drain Router-Held User Funds — (File: metric-periphery/contracts/base/PeripheryPayments.sol)

## Summary
`PeripheryPayments` exposes three balance-draining helpers — `unwrapWETH9`, `sweepToken`, and `refundETH` — with no caller restrictions and caller-controlled `recipient` parameters. Two concrete production paths strand user funds on the router between transactions: excess native ETH left by the WETH-input branch of `pay()`, and WETH routed to `address(router)` for a deferred `unwrapWETH9` call. Any unprivileged third party can immediately drain those stranded balances, causing direct loss of user principal.

## Finding Description
All three helpers are confirmed unrestricted in the production code:

`unwrapWETH9` (L37–45) reads the router's full WETH balance and transfers it to a caller-supplied `recipient` with no check that the caller deposited those funds.
`sweepToken` (L48–55) does the same for any ERC-20 token.
`refundETH` (L58–63) sends the router's entire native ETH balance to `msg.sender`.

**Path A — excess ETH stranded by `pay()`.**
`exactInputSingle` is `external payable` (L67), so callers may send `msg.value > params.amountIn`. Inside the swap callback, `_justPayCallback` calls `pay(WETH, payer, pool, value)`. The WETH branch of `pay()` (L73–77) wraps exactly `value` ETH and forwards it to the pool; the surplus `address(this).balance − value` ETH is never returned and remains on the router. No automatic refund exists. A subsequent call to `refundETH()` by any address drains that surplus to `msg.sender`.

**Path B — WETH stranded between swap and unwrap.**
The documented ETH-output pattern (confirmed by `test_multicall_tokenForWeth_thenUnwrapEth`, L135–162) routes swap output to `address(router)` as the `recipient`, then calls `unwrapWETH9` in the same `multicall`. If a user splits these into two separate transactions, WETH sits on the router between them. Any caller can front-run the second transaction with `sweepToken(WETH, 0, attacker)` or `unwrapWETH9(0, attacker)`, redirecting the full WETH balance to an attacker-chosen address.

No guard in any of the three functions verifies that `msg.sender` is the depositor, nor does any transient-storage mechanism record the rightful beneficiary.

## Impact Explanation
Direct loss of user principal. In Path A the loss equals `msg.value − amountIn` per affected call; in Path B the loss equals the full swap output. Both amounts can be arbitrarily large (bounded only by the user's trade size), well above Sherlock High/Critical thresholds. The corrupted values are `address(router).balance` (Path A) and `IERC20(WETH).balanceOf(address(router))` (Path B).

## Likelihood Explanation
The attack requires no privileges, no special setup, and no capital. MEV bots routinely scan for non-zero router balances and can atomically call `refundETH()` or `sweepToken()` in the same block as the victim's transaction. Any user who calls `exactInputSingle` directly (not via `multicall`) with `msg.value > amountIn`, or who splits a WETH-output swap and unwrap across two transactions, is immediately exploitable. Because `exactInputSingle` and `exactOutputSingle` are `external payable`, direct ETH-input use is an expected and documented pattern, making Path A a realistic user mistake.

## Recommendation
1. Record the depositor address in transient storage at swap entry and enforce it inside `refundETH`, `sweepToken`, and `unwrapWETH9` so only the originating caller can claim residue.
2. Alternatively, remove the arbitrary `recipient` parameter from `sweepToken` and `unwrapWETH9` and replace it with the transient-storage-recorded beneficiary.
3. For Path A specifically, auto-refund excess native ETH at the end of each top-level swap function rather than relying on the user to batch a `refundETH()` call.

## Proof of Concept
**Scenario 1 — ETH theft via `refundETH`:**
1. Alice calls `router.exactInputSingle{value: 2 ether}(ExactInputSingleParams{ tokenIn: WETH, amountIn: 1 ether, … })` directly (no multicall).
2. Inside `_justPayCallback → pay(WETH, alice, pool, 1 ether)`: `nativeBalance = 2 ether ≥ value = 1 ether`; wraps 1 ETH → WETH → pool; **1 ETH remains on router** (L74–77).
3. Bob calls `router.refundETH()` in a separate transaction.
4. `refundETH` sends `address(this).balance = 1 ether` to `msg.sender` (Bob). Alice loses 1 ETH.

**Scenario 2 — WETH theft via `sweepToken`:**
1. Alice calls `router.exactInputSingle(ExactInputSingleParams{ tokenOut: WETH, recipient: address(router), … })` — WETH swap output lands on the router.
2. Alice intends to call `router.unwrapWETH9(0, alice)` in a follow-up transaction.
3. Bob front-runs Alice's second transaction with `router.sweepToken(WETH, 0, bob)`.
4. `sweepToken` transfers the entire WETH balance to Bob (L52–54). Alice's full swap output is stolen. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-78)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```
