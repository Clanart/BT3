The code confirms all claims. Let me verify the key facts:

1. `refundETH()` at lines 58-63 of `PeripheryPayments.sol` — no access control, sends full ETH balance to `msg.sender`.
2. `pay()` at lines 73-77 — when `nativeBalance >= value`, wraps exactly `value` ETH, leaving the surplus on the contract.
3. `receive()` at lines 32-34 — only blocks unsolicited direct ETH pushes; does not prevent accumulation via `msg.value` in payable entry points.
4. `exactInputSingle` at line 67 is `external payable` and callable directly without `multicall`.
5. The test at lines 106-133 confirms the intended safe pattern requires bundling `refundETH()` in the same `multicall`, but this is not enforced.

The exploit path is real and complete: victim calls `exactInputSingle{value: amountIn + excess}` directly → `pay()` wraps only `amountIn` → `excess` sits on router → attacker calls `refundETH()` → steals `excess`.

---

Audit Report

## Title
Unprivileged `refundETH()` drains residual ETH left by excess `msg.value` in direct payable swap calls — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.refundETH()` is a public function with no caller restriction that transfers the router's entire ETH balance to `msg.sender`. Every swap entry point (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`) is independently `payable`, so a user who sends `msg.value > amountIn` without bundling a `refundETH()` call in the same `multicall` leaves residual ETH on the router. Any third party can immediately call `refundETH()` and receive that ETH.

## Finding Description
`refundETH()` (L58-63 of `PeripheryPayments.sol`) unconditionally sends `address(this).balance` to `msg.sender` with no depositor check, no per-caller accounting, and no reentrancy guard relevant to the theft path. The `receive()` guard (L32-34) only blocks unsolicited direct ETH pushes from non-WETH addresses; it does not prevent ETH from accumulating via `msg.value` in payable swap calls. When `exactInputSingle` is called directly with `msg.value > amountIn` and `tokenIn == WETH`, the `pay()` function (L73-77) wraps exactly `amountIn` ETH into WETH and transfers it to the pool, leaving `msg.value - amountIn` on the router. The function returns without refunding the surplus. Any subsequent call to `refundETH()` — from any EOA — drains the full balance. The intended safe pattern (bundle swap + `refundETH()` in one `multicall`) is demonstrated in the test at L106-133 but is not enforced at the contract level.

## Impact Explanation
Direct theft of user ETH. A victim who sends `msg.value = 2 ether` for a 1 ether WETH swap loses 1 ether to any attacker who calls `refundETH()` before the victim does. Impact is proportional to the excess ETH sent; there is no floor. This is a direct loss of user principal satisfying the High severity threshold.

## Likelihood Explanation
Moderate-to-high. Users interacting via a frontend or script that calls `exactInputSingle` directly (not via `multicall`) with a rounded-up or over-estimated `msg.value` are vulnerable. MEV bots routinely monitor for unprotected ETH on router contracts and can back-run in the same block. No special privileges or setup are required — any EOA can call `refundETH()`.

## Recommendation
Track the depositor in transient storage at the top of each payable entry point and restrict `refundETH()` to that address, clearing it after the refund. Concretely: at the start of `exactInputSingle`, `exactOutputSingle`, `exactInput`, and `exactOutput`, write `msg.sender` into a transient slot (e.g., `DEPOSITOR_SLOT`); in `refundETH()`, require `msg.sender == tload(DEPOSITOR_SLOT)` and clear the slot after the transfer. This is the pattern used by Uniswap v3 successors and is safe within `multicall` because delegatecall preserves `msg.sender`.

## Proof of Concept
```solidity
function test_attacker_steals_excess_eth() public {
    address victim   = makeAddr("victim");
    address attacker = makeAddr("attacker");
    uint128 amountIn = 1_000;
    uint256 excess   = 1 ether;

    vm.deal(victim, amountIn + excess);

    // Victim calls exactInputSingle directly — no multicall + refundETH bundle
    vm.prank(victim);
    router.exactInputSingle{value: amountIn + excess}(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: amountIn,
            amountOutMinimum: 0,
            recipient: victim,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );

    // Router holds `excess` ETH — victim forgot to bundle refundETH
    assertEq(address(router).balance, excess);

    uint256 attackerBefore = attacker.balance;

    // Attacker steals it
    vm.prank(attacker);
    router.refundETH();

    assertEq(attacker.balance - attackerBefore, excess, "attacker stole victim ETH");
    assertEq(address(router).balance, 0);
}
```