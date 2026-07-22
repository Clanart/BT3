The vulnerability is real. The `pay` function's WETH branch reads `address(this).balance` without any per-caller accounting, so any ETH stranded from a prior multicall is silently consumed by the next WETH swap.

---

### Title
Stranded ETH from a prior multicall is silently consumed by any subsequent WETH swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.pay` uses `address(this).balance` as a first-priority funding source whenever `token == WETH`. Because `multicall` is `payable` and `refundETH` is optional, ETH left over from one user's multicall sits unguarded in the router. The very next WETH-input swap by any caller will wrap and spend that ETH instead of pulling WETH from the new caller's wallet, permanently destroying the original depositor's funds.

### Finding Description
`exactInputSingle` (and every other swap entry-point) records `msg.sender` as `payer` in transient storage, then calls the pool. The pool calls back into `metricOmmSwapCallback`, which calls `_justPayCallback`, which calls `pay(tokenIn, payer=msg.sender, pool, value)`. [1](#0-0) 

Inside `pay`, the WETH branch is:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ← entire router balance, no per-user tracking
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
``` [2](#0-1) 

`address(this).balance` is the **global** router balance. There is no slot that records how much ETH the current `msg.sender` deposited. Any ETH sitting in the router — regardless of who sent it — is treated as available collateral for the current swap.

`refundETH` sends the entire balance back to `msg.sender`, but it is a separate, optional call that users must explicitly include in their multicall: [3](#0-2) 

`multicall` itself imposes no requirement that ETH is fully consumed: [4](#0-3) 

### Impact Explanation
Direct, permanent loss of ETH for any user who sends more ETH than their swap consumes and omits `refundETH`. The attacker pays zero WETH from their own wallet; the victim's ETH is wrapped and forwarded to the pool on the attacker's behalf. The attacker receives full swap output; the victim receives nothing in return for the stolen ETH. Impact is **High**.

### Likelihood Explanation
- Users are expected to call `refundETH` as a best practice, but the router provides no enforcement.
- A single-call `exactInputSingle{value: X}(...)` without a wrapping multicall leaves no path to refund — the ETH is stranded immediately.
- An attacker can watch the mempool (or simply call after any block where the router holds ETH) and issue a WETH swap sized to exactly the stranded amount, costing only gas.
- Likelihood is **Medium** (requires a victim mistake, but the exploit is trivially executable once the precondition exists).

### Recommendation
Track per-call ETH with a transient variable set to `msg.value` at each `payable` entry-point, and consume only up to that amount in `pay`. Alternatively, require that `msg.value == 0` for non-ETH swaps and only allow the ETH path when `msg.value > 0` and the current call is the one that deposited it. A simpler mitigation is to snapshot `msg.value` into a transient slot at entry and subtract from it in `pay`, reverting if the router's balance exceeds what the current caller deposited.

### Proof of Concept
1. User A calls `router.multicall{value: 1 ether}([exactInputSingle(WETH→token, amountIn=0.9 ether, ...)])` — no `refundETH` appended. Swap succeeds; router now holds `0.1 ether`.
2. Attacker calls `router.exactInputSingle{value: 0}(ExactInputSingleParams{tokenIn: WETH, amountIn: 0.1 ether, ...})`.
3. Pool callback fires → `pay(WETH, attacker, pool, 0.1 ether)`.
4. `nativeBalance = 0.1 ether >= value = 0.1 ether` → router wraps User A's ETH and transfers WETH to pool.
5. Attacker receives full swap output; `IERC20(WETH).safeTransferFrom(attacker, ...)` is never reached.
6. `assert router.balance == 0` and User A's `0.1 ether` is gone with no recourse.

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L192-199)
```text
  function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
      _getTokenToPay(),
      _getPayer(),
      msg.sender,
      uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-84)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
      } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
      }
```
