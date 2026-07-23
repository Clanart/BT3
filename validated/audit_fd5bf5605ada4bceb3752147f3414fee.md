The code is confirmed. Let me verify the callback flow to ensure the full attack path is reachable.

Audit Report

## Title
Prior-caller ETH silently consumed by subsequent WETH swap — `PeripheryPayments.pay` partial-ETH branch — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay` reads `address(this).balance` as the native ETH contribution toward a WETH payment. This is the router's **global** ETH balance, not the ETH sent with the current call. Any ETH stranded on the router from a prior caller's excess `msg.value` (when `refundETH()` was omitted) is silently consumed by the next caller who swaps with `tokenIn = WETH`, reducing that caller's WETH pull by the stranded amount and permanently stealing the prior depositor's ETH.

## Finding Description
The vulnerable branch in `pay` is at lines 78–81:

```solidity
uint256 nativeBalance = address(this).balance;   // global router balance
...
} else if (nativeBalance > 0) {
    IWETH9(WETH).deposit{value: nativeBalance}();
    IERC20(WETH).safeTransfer(recipient, nativeBalance);
    IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
}
```

`address(this).balance` is the router's total ETH, not `msg.value` of the current call. ETH accumulates on the router whenever a user calls a payable entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`) with `msg.value > amountIn` and omits `refundETH()`. The `receive()` guard (line 32–34) only blocks direct ETH pushes from non-WETH addresses; it does not prevent excess `msg.value` from accumulating.

**Full call path:**
1. Victim calls `multicall{value: 1 ether}([exactInputSingle(tokenIn=WETH, amountIn=0.3 ether)])` without appending `refundETH()`. The swap consumes 0.3 ETH; 0.7 ETH remains on the router.
2. Attacker calls `exactInputSingle(tokenIn=WETH, amountIn=1 ether)` with zero `msg.value`.
3. `exactInputSingle` stores `msg.sender` (attacker) as payer via `_setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)` (line 71 of `MetricOmmSimpleRouter.sol`).
4. Pool calls back `metricOmmSwapCallback` → `_justPayCallback` → `pay(WETH, attacker, pool, 1 ether)`.
5. Inside `pay`: `nativeBalance = 0.7 ether` (victim's residual). Middle branch fires: deposits 0.7 ETH as WETH to pool; pulls only `1 - 0.7 = 0.3 WETH` from attacker's allowance.
6. Pool receives 1 WETH total. Attacker pays 0.3 WETH instead of 1 WETH. Victim's 0.7 ETH is gone.

Existing guards are insufficient: `receive()` only blocks direct pushes; `refundETH()` is optional and not enforced at entry points; no per-call ETH accounting exists.

## Impact Explanation
Direct, irreversible loss of user principal (ETH). Any ETH stranded on the router — a realistic user error explicitly demonstrated by the test suite — is unconditionally transferred to the next WETH-input swap caller. The victim cannot recover the ETH once the attacker's transaction executes. This is an unprivileged theft of another user's ETH with no protocol-level guard, meeting the High direct-loss-of-user-principal threshold.

## Likelihood Explanation
**Medium.** The precondition — residual ETH on the router — requires a user to send excess `msg.value` without `refundETH()`. The test `test_multicall_ethInput_exactInputSingle_refundsUnusedEth` (lines 106–133) demonstrates this is a natural usage pattern; the correct multicall includes `refundETH()` as a second call, and omitting it is a realistic mistake. A MEV bot can monitor the router's ETH balance on-chain or watch the mempool for victim transactions and front-run any subsequent `refundETH()` call with a WETH swap that drains the balance.

## Recommendation
Track only the ETH contributed by the current top-level call rather than the router's total balance. Replace `address(this).balance` in `pay` with a transient slot that is written at entry to each payable external function and cleared at exit:

```solidity
// At entry to exactInputSingle, exactInput, exactOutputSingle, exactOutput, multicall:
_storeMsgValue(msg.value);   // write msg.value to a transient slot

// In pay(), replace:
uint256 nativeBalance = address(this).balance;
// With:
uint256 nativeBalance = _consumeMsgValue(value); // read and zero the transient slot
```

Alternatively, enforce `msg.value == 0 || msg.value == amountIn` at every payable entry point and revert on mismatch, eliminating the possibility of stranded ETH entirely.

## Proof of Concept
```solidity
// 1. Victim sends excess ETH, forgets refundETH
router.multicall{value: 1 ether}(
    [abi.encodeCall(router.exactInputSingle, (ExactInputSingleParams({
        tokenIn: WETH, amountIn: 0.3 ether, ...
    })))]
);
// Router now holds 0.7 ETH

// 2. Attacker approves only 0.3 WETH
weth.approve(address(router), 0.3 ether);

// 3. Attacker swaps 1 WETH — only 0.3 WETH pulled from allowance
router.exactInputSingle(ExactInputSingleParams({
    tokenIn: WETH, amountIn: 1 ether, ...
}));

// Pool received 1 WETH; attacker paid 0.3 WETH; victim lost 0.7 ETH
assert(address(router).balance == 0);
assert(weth.balanceOf(attacker) == initialWeth - 0.3 ether);
```