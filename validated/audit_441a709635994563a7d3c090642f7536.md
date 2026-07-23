Audit Report

## Title
Stranded Native ETH from Excess `msg.value` in WETH Swap Functions Is Permanently Claimable by Any Caller via `refundETH()` / `pay()` — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
The `pay()` function in `PeripheryPayments.sol` uses `address(this).balance` — the router's entire native ETH balance — when settling a WETH swap obligation, with no per-caller attribution. All four payable swap entry-points (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`) accept `msg.value` but never automatically refund excess ETH. Any ETH left over after a WETH swap is permanently stranded on the router and can be stolen by any unprivileged caller via the access-control-free `refundETH()`, or silently consumed on behalf of an attacker's subsequent WETH swap.

## Finding Description

**Root cause — `pay()` consumes the router's global ETH balance without per-caller attribution:**

In `PeripheryPayments.sol` lines 73–84, when `token == WETH` and `payer != address(this)`, the function reads `address(this).balance` — the router's total ETH — and uses it to wrap and transfer WETH to the pool. There is no check that this ETH was deposited by the current payer in the current transaction:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // entire router balance
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value); // payer charged nothing
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

**Stranding mechanism — payable swap functions never auto-refund excess ETH:**

`exactInputSingle` (L67), `exactInput` (L92), `exactOutputSingle` (L130), and `exactOutput` (L154) in `MetricOmmSimpleRouter.sol` are all `external payable`. Each ends with `_clearExpectedCallbackPool()` but never calls `refundETH()`. The most realistic stranding path is `exactOutputSingle` with WETH as `tokenIn`: the caller must send up to `amountInMaximum` ETH because the exact input is unknown before execution. The actual input is almost always less than `amountInMaximum`, leaving the difference stranded on the router.

The `receive()` guard at L32–34 (`if (msg.sender != WETH) revert NotWETH()`) prevents direct ETH deposits but does **not** prevent excess `msg.value` from payable function calls — ETH stranding is reachable through normal usage.

**Theft mechanism — `refundETH()` has no access control:**

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);  // any caller, entire balance
    }
}
```

Any address can call `refundETH()` at any time and receive the router's full ETH balance.

**Exploit flow:**
1. Alice calls `exactOutputSingle{value: 1 ether}(tokenIn=WETH, amountOut=500, amountInMaximum=1 ether)`.
2. Pool executes; actual WETH input = 600 wei. `pay()` finds `address(this).balance (1 ether) >= 600`, wraps 600 wei, sends WETH to pool.
3. Router ETH balance = `1 ether - 600 wei` (stranded). Alice receives output tokens; her ETH is gone.
4. Bob calls `router.refundETH()` → receives `~1 ether` for free.

Alternatively, Bob calls `exactInputSingle(tokenIn=WETH, amountIn=stranded_amount)` with `msg.value = 0`. `pay()` finds `address(this).balance >= value`, wraps Alice's stranded ETH, and Bob receives output tokens without spending any ETH or WETH.

## Impact Explanation
Direct loss of user principal — High severity. A victim loses 100% of their excess `msg.value` (the difference between `amountInMaximum` and the actual swap input). For `exactOutputSingle` with a large `amountInMaximum` buffer (standard in production integrations), the stranded amount can be substantial. Both theft vectors (direct `refundETH()` drain and free WETH swap) require zero capital from the attacker and result in complete loss of the stranded ETH for the victim.

## Likelihood Explanation
- Sending `amountInMaximum` ETH with `exactOutputSingle` is the standard and documented usage pattern; the test suite itself demonstrates it with `refundETH` in a multicall, confirming the stranding is expected when `refundETH` is omitted.
- Users calling swap functions directly (not via multicall) have no automatic refund and no on-chain warning.
- `refundETH()` is `external` with no `onlyOwner` or caller check — any EOA or contract can call it.
- The attack is MEV-extractable: a searcher can bundle `refundETH()` immediately after any transaction that leaves ETH on the router, in the same block.

## Recommendation
**Short term:**
1. In `pay()`, when `token == WETH` and `payer != address(this)`, only consume `msg.value` from the current call frame (e.g., track it via a transient storage slot set at swap entry) rather than `address(this).balance`.
2. Add an automatic `refundETH()` call at the end of each payable swap function, or assert `msg.value == 0` in non-WETH swap paths.

**Long term:**
1. Restrict `refundETH()` so it can only be called within a `multicall` originating from the same `msg.sender` who deposited the ETH, or remove it as a standalone external function.
2. Require that any payable swap function consuming native ETH explicitly refunds the remainder before returning, rather than relying on the caller to append a `refundETH()` step.

## Proof of Concept
```
Setup:
  - Router deployed with WETH and Factory
  - Pool: WETH / TokenB registered in Factory
  - Alice has 1 ETH; Bob has 0 ETH

Step 1 — Alice strands ETH:
  Alice calls router.exactOutputSingle{value: 1 ether}(
      pool=pool, tokenIn=WETH, tokenOut=TokenB,
      zeroForOne=true, amountOut=500, amountInMaximum=1 ether,
      recipient=alice, deadline=..., priceLimitX64=0, extensionData=""
  )
  → Pool executes; actual WETH input = 600 wei
  → pay(WETH, alice, pool, 600):
      nativeBalance = 1 ether ≥ 600 → wraps 600 wei, sends WETH to pool
  → Router ETH balance = 1 ether - 600 wei (stranded)
  → Alice receives 500 TokenB; Alice's ETH balance = 0

Step 2 — Bob steals stranded ETH:
  Bob calls router.refundETH()
  → balance = 1 ether - 600 wei
  → _transferETH(bob, balance)
  → Bob receives ~1 ETH for free

Result:
  Alice lost ~1 ETH (excess msg.value)
  Bob gained ~1 ETH with zero capital
```