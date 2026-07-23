### Title
Unrestricted `sweepToken`, `unwrapWETH9`, and `refundETH` Allow Any Caller to Drain Router-Held User Funds — (File: metric-periphery/contracts/base/PeripheryPayments.sol)

---

### Summary

`PeripheryPayments.sweepToken`, `unwrapWETH9`, and `refundETH` are all public/external with no access control and accept caller-controlled `recipient` parameters. The `pay()` function deliberately leaves excess native ETH on the router when `msg.value > amountIn` (WETH-input path), and the standard WETH-output-to-router pattern leaves WETH on the router between a swap and an unwrap step. Because any caller can invoke these helpers at any time and redirect the full router balance to any address, stranded user funds are directly stealable by an unprivileged third party.

---

### Finding Description

`PeripheryPayments` exposes three balance-draining helpers with zero caller restrictions:

```solidity
// PeripheryPayments.sol L37-55, L58-63
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override { … }
function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override { … }
function refundETH() external payable override { … }
```

Each function transfers the **entire** router balance of the relevant asset to a caller-chosen address (or `msg.sender` for `refundETH`). No check verifies that the caller is the user who deposited the funds.

Two concrete paths strand funds on the router:

**Path A — excess native ETH from WETH-input swap.**
Inside `pay()`, when `token == WETH` and `nativeBalance >= value`, only `value` ETH is wrapped and forwarded to the pool; the remainder stays on the router as native ETH:

```solidity
// PeripheryPayments.sol L74-77
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
}   // ← nativeBalance - value ETH is now stranded
```

A user who calls `exactInputSingle{value: X}(amountIn: Y, tokenIn: WETH)` with `X > Y` without batching `refundETH()` in the same multicall leaves `X − Y` ETH on the router.

**Path B — WETH stranded from swap-output-to-router pattern.**
The standard WETH-unwrap flow routes swap output to `address(router)` first, then calls `unwrapWETH9`. If these two steps are not atomic (i.e., not in the same `multicall`), WETH sits on the router between transactions.

In both cases, any third party can immediately call:
- `refundETH()` → steals all native ETH, sent to `msg.sender`
- `sweepToken(WETH, 0, attacker)` or `unwrapWETH9(0, attacker)` → steals all WETH, sent to attacker-chosen address
- `sweepToken(anyToken, 0, attacker)` → steals any ERC20 stranded on the router

---

### Impact Explanation

**Direct loss of user principal.** A user who:
1. Sends `msg.value > amountIn` in a WETH-input swap without `refundETH()` in the same multicall loses the entire excess ETH to the first caller of `refundETH()`.
2. Routes a WETH-output swap to `address(router)` without `unwrapWETH9()` in the same multicall loses the entire swap output to the first caller of `sweepToken(WETH, 0, attacker)` or `unwrapWETH9(0, attacker)`.

Loss magnitude equals the stranded amount, which can be the full swap output — well above Sherlock High/Critical thresholds.

---

### Likelihood Explanation

**Medium-High.** The attack is unprivileged and requires no special setup. MEV bots routinely monitor the mempool for exactly this pattern (stranded router balances). Any user who calls `exactInputSingle` or `exactOutputSingle` directly (not via `multicall`) with excess ETH, or who splits the swap and unwrap into two separate transactions, is immediately exploitable. The `exactInputSingle` and `exactOutputSingle` functions are `external payable`, implying direct ETH-input use is expected, yet the router provides no automatic refund.

---

### Recommendation

1. **Restrict `refundETH` to `msg.sender` within a multicall context only**, or record the depositor address in transient storage and enforce it in the cleanup helpers.
2. **Remove the arbitrary `recipient` parameter from `sweepToken` and `unwrapWETH9`**, replacing it with a transient-storage-recorded beneficiary set at swap entry, so only the originating caller can claim the residue.
3. Alternatively, auto-refund excess native ETH at the end of each top-level swap function rather than relying on the user to batch a cleanup call.

---

### Proof of Concept

**Scenario 1 — ETH theft via `refundETH`:**

1. Alice calls `router.exactInputSingle{value: 2 ether}(ExactInputSingleParams{ tokenIn: WETH, amountIn: 1 ether, … })` directly (no multicall).
2. Inside `metricOmmSwapCallback → _justPayCallback → pay(WETH, alice, pool, 1 ether)`:
   - `nativeBalance = 2 ether >= value = 1 ether`
   - Wraps 1 ETH → WETH → pool; **1 ETH remains on router**.
3. Bob calls `router.refundETH()` in a separate transaction.
4. `refundETH` sends `address(this).balance = 1 ether` to `msg.sender` (Bob).
5. Alice loses 1 ETH with no recourse.

**Scenario 2 — WETH theft via `sweepToken`:**

1. Alice calls `router.exactInputSingle(ExactInputSingleParams{ tokenIn: token1, tokenOut: WETH, recipient: address(router), … })` — WETH swap output lands on the router.
2. Alice intends to call `router.unwrapWETH9(0, alice)` in a follow-up transaction.
3. Bob front-runs Alice's second transaction with `router.sweepToken(WETH, 0, bob)`.
4. `sweepToken` transfers the entire WETH balance (`IERC20(WETH).balanceOf(address(router))`) to Bob.
5. Alice's full swap output is stolen. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
