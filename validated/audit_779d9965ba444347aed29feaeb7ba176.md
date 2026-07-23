The vulnerability is real. Let me trace the exact code path.

The `pay` function in `PeripheryPayments.sol` has a WETH branch with three sub-cases: [1](#0-0) 

When `token == WETH` and `payer != address(this)`, the function reads `address(this).balance` and, if `0 < nativeBalance < value`, it:
1. Deposits and transfers `nativeBalance` ETH (from whoever left it) to the recipient
2. Pulls only `value - nativeBalance` WETH from `payer` via `transferFrom`

ETH can be stranded in the router between transactions because `multicall` is `payable`: [2](#0-1) 

The `receive()` guard only blocks direct ETH transfers (not ETH sent with a function call), so ETH sent with `multicall` that is not fully consumed by a swap — and where the user omits `refundETH` — remains in the contract after the transaction: [3](#0-2) 

The `exactInputSingle` flow sets `payer = msg.sender` and calls `pay` from `_justPayCallback`: [4](#0-3) [5](#0-4) 

There is no check that the ETH in `address(this).balance` was deposited by the current `payer`. Any stranded ETH from any prior user is silently consumed.

---

### Title
Stranded ETH in router consumed to partially fund a different user's WETH swap, causing permanent ETH loss — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.pay` uses the router's entire native ETH balance to partially cover a WETH payment for the current caller, without verifying that the ETH belongs to that caller. ETH left in the router by a prior user (via `multicall` without `refundETH`) is silently consumed.

### Finding Description
The WETH branch of `pay` (lines 78–81) checks `address(this).balance` and, when `0 < nativeBalance < value`, wraps and forwards the full native balance to the pool, then pulls only the remainder from `payer` via `transferFrom`. The native balance is a shared, unattributed pool of ETH. Any ETH stranded from a prior multicall (e.g., a user sent excess `msg.value` and omitted `refundETH`) is indistinguishable from ETH intentionally deposited by the current payer. [6](#0-5) 

### Impact Explanation
The stranded ETH owner suffers a permanent, unrecoverable loss of principal. The current swapper benefits by needing a smaller WETH allowance than the full `amountIn`. This is a direct loss of user funds above Sherlock's Critical/High threshold.

### Likelihood Explanation
ETH stranding is a realistic user error: sending `msg.value > amountIn` in a multicall without appending `refundETH`. An attacker can monitor the router's ETH balance on-chain and time a WETH swap to drain it. No privileged access, malicious pool, or non-standard token is required — only a public `exactInputSingle` call with `tokenIn = WETH`.

### Recommendation
Remove the partial-ETH-cover logic entirely. If the caller intends to pay with native ETH, they should wrap it themselves before calling, or the router should only use ETH that was sent in the **same transaction** (tracked via a transient variable set at the top of `exactInputSingle`/`multicall` entry). The simplest fix: in the WETH branch, only use `address(this).balance` when it was explicitly sent as `msg.value` in the current call (compare against a transient `msgValue` slot), and revert or ignore it otherwise.

### Proof of Concept
1. userB calls `router.multicall{value: 0.5 ether}([exactInputSingle(tokenIn=WETH, amountIn=0.5 ether, ...)])` — the swap uses all 0.5 ETH, but userB sends 1 ETH and omits `refundETH`. 0.5 ETH is stranded.
2. userA calls `router.exactInputSingle(tokenIn=WETH, amountIn=1 ether, ...)` with only 0.5 WETH approved.
3. `pay(WETH, userA, pool, 1e18)` is called. `nativeBalance = 0.5 ETH`, `0 < 0.5 < 1`, so the `else if` branch fires: wraps and sends 0.5 ETH, then pulls 0.5 WETH from userA.
4. userA's swap succeeds with only 0.5 WETH approved. userB's 0.5 ETH is permanently lost.

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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
