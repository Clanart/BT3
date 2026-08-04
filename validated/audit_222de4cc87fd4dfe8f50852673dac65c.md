Based on my analysis of the `IntrinsicIntents.sol` same-chain fill path, I found a credible local analog to the rounding/short-payout bug class, but it's actually the inverse — an **over-release / fund-shortfall on a specific storage key** rather than a simple rounding-direction bug, rooted in how `escrowedAmount` is computed on the "final chunk" of a partial fill.

I was not able to fully verify (tool budget exhausted) whether `IntentGatewayV2.sol::placeOrder` rejects orders whose `inputs[]` array contains a duplicate token address — my `grep_search` for `duplicate|unique token|seen[|tokenSeen` across `evm/src/apps/intentsv2/**` returned no matches, and `IntentsBase.sol`'s `_orders` mapping is keyed only by `(commitment, token)`, not by index — but I could not read `IntentGatewayV2.placeOrder` in full to confirm there is no de-duplication check upstream. Given this specific uncertainty, I cannot state with full confidence that this path is reachable by an unprivileged attacker without deeper verification of `placeOrder`'s input validation.

### Title
Escrow keyed by `(commitment, token)` instead of per-pair index lets a repeated input token in one order over-release escrow to the first completing output pair - ([File: evm/src/apps/intentsv2/IntrinsicIntents.sol])

### Summary
`_fillSameChain` in `IntrinsicIntents.sol` computes the escrow amount to release for output-pair `i` as either a proportional floor-division amount, or — when that specific pair's cumulative fill reaches `totalRequired` — the **entire current balance** of `_orders[commitment][inputToken]` [1](#0-0) . Since `_orders` is keyed only by `(commitment, token address)` and not by pair index, if an order's `inputs[]` array contains the same token address at two different indices (e.g., `inputs[0].token == inputs[2].token`, both USDC, paired with two different output tokens), the first pair to reach full-fill status will sweep the **combined** remaining escrow for that token across both pairs, rather than just its own share.

### Finding Description
`IntentsBase._orders` is declared as `mapping(bytes32 => mapping(address => uint256))` — keyed by commitment and token address only [2](#0-1) . During fills, `_withdraw` decrements this balance by the exact amount released [3](#0-2) .

In `_fillSameChain`, for each output index `i`, the escrow amount is chosen as:
```solidity
uint256 escrowedAmount;
if (amountFilled == totalRequired) {
    escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
} else {
    escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
}
``` [1](#0-0) 

This "full-balance" branch was added (per the rounding-dust test comments, `testPartialFill_RoundingDustReleasedToFinalSolver`) specifically to avoid leaving floor-division dust locked when a single output-token pair's *own* fills complete [4](#0-3) . That fix is safe under the *implicit* assumption that each input token address in the order appears exactly once (1:1 with an output). If a single order specifies the same input token twice (mapped to two different output assets), completing the *first* of the two output pairs would pull `_orders[commitment][token]`, which by then holds the combined remaining escrow of **both** pairs, not just the first pair's remaining share.

### Impact Explanation
If reachable, this allows a solver completing one output leg of a multi-asset order to receive escrow that rightfully belongs to a different, not-yet-filled leg of the same order — a wrong-beneficiary/wrong-amount fund diversion. The user (order owner) would be left unable to have the second leg filled (the corresponding `_orders` entry would read `0`, causing subsequent fill attempts to revert with `UnknownOrder()`), effectively locking that portion of the trade and letting the first solver capture funds beyond their entitled share.

### Likelihood Explanation
This requires an order with a duplicated input-token address across different output pairs — an unusual but not obviously prohibited order shape given the `Order`/`TokenInfo[]` structure and the absence of any duplicate-token check found in `IntentsBase.sol` or `IntrinsicIntents.sol`. I could not confirm from available tool calls whether `IntentGatewayV2.sol::placeOrder` (not indexed in detail during this session) performs such a de-duplication check before escrow, which is the deciding factor for real-world exploitability. This is a material gap in my verification.

### Recommendation
Key `_orders` by `(commitment, token, index)` or equivalently maintain a per-output-pair escrow reservation instead of a token-address-only balance, so that a completed pair can only claim its own reserved remainder. Alternatively, explicitly reject orders whose `inputs[]` contains duplicate token addresses at `placeOrder` time.

### Proof of Concept
Not independently constructed as a runnable test in this session due to tool-call exhaustion; the reasoning above is derived directly from the cited contract logic (`escrowedAmount = _orders[commitment][token]` on pair completion, combined with `_orders` being keyed only by token address). A concrete PoC would need to: (1) confirm `placeOrder` permits `inputs[0].token == inputs[1].token` with distinct `output.assets[0]`/`output.assets[1]`, (2) place such an order, (3) fully fill output pair 0 first, and (4) assert `_orders[commitment][token]` transferred to that solver exceeds `inputs[0].amount`, leaving pair 1's fill unable to proceed.

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L116-122)
```text
            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
            escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L136-140)
```text
    /**
     * @dev Maps (commitment, token address) to the escrowed amount for that token.
     * Decremented as tokens are released via fills or refunds.
     */
    mapping(bytes32 => mapping(address => uint256)) public _orders;
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L400-403)
```text
            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
```

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L1543-1657)
```text
    /*//////////////////////////////////////////////////////////////
                    ROUNDING DUST IN PARTIAL FILLS (Finding #4)
    //////////////////////////////////////////////////////////////*/

    /// @notice Verifies that rounding dust from integer division in partial fills
    /// is not permanently locked. The final solver completing the order should
    /// receive the full remaining escrow balance rather than a truncated amount.
    function testPartialFill_RoundingDustReleasedToFinalSolver() public {
        // Choose amounts that produce rounding truncation:
        // input = 100 USDC (100e6), output = 3 DAI (3e18)
        // Each of 3 solvers fills 1 DAI. Proportional release per fill:
        //   100e6 * 1e18 / 3e18 = 33333333 (truncated from 33333333.33...)
        // Without fix: 3 * 33333333 = 99999999, leaving 1 unit locked.
        // With fix: final solver gets remaining balance = 100e6 - 2*33333333 = 33333334
        uint256 inputAmount = 100 * 1e6; // 100 USDC
        uint256 outputAmount = 3 * 1e18; // 3 DAI

        TokenInfo[] memory inputs = new TokenInfo[](1);
        inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: inputAmount});

        TokenInfo[] memory outputAssets = new TokenInfo[](1);
        outputAssets[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: outputAmount});

        PaymentInfo memory output =
            PaymentInfo({beneficiary: bytes32(uint256(uint160(user))), assets: outputAssets, call: ""});

        Order memory order = Order({
            user: bytes32(0),
            source: "",
            destination: host.host(),
            deadline: block.number + 100,
            nonce: 0,
            fees: 0,
            session: address(0),
            predispatch: DispatchInfo({assets: new TokenInfo[](0), call: ""}),
            inputs: inputs,
            output: output
        });

        // User places order
        vm.startPrank(user);
        usdc.approve(address(intentGateway), inputAmount);
        intentGateway.placeOrder(order, bytes32(0));
        vm.stopPrank();

        order.user = bytes32(uint256(uint160(user)));
        order.source = host.host();
        order.nonce = 0;

        uint256 fillPerSolver = 1e18; // Each solver fills 1 DAI
        uint256 truncatedRelease = (inputAmount * fillPerSolver) / outputAmount; // 33333333

        // --- Solver 1 fills 1 DAI ---
        address solver1 = makeAddr("solver1");
        vm.deal(solver1, 1 ether);
        deal(address(dai), solver1, 10 * 1e18);
        uint256 solver1UsdcBefore = usdc.balanceOf(solver1);

        vm.startPrank(solver1);
        dai.approve(address(intentGateway), fillPerSolver);
        TokenInfo[] memory out1 = new TokenInfo[](1);
        out1[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: fillPerSolver});
        intentGateway.fillOrder(order, FillOptions({relayerFee: 0, nativeDispatchFee: 0, outputs: out1}));
        vm.stopPrank();

        assertEq(
            usdc.balanceOf(solver1),
            solver1UsdcBefore + truncatedRelease,
            "Solver1 should receive truncated proportional USDC"
        );

        // --- Solver 2 fills 1 DAI ---
        address solver2 = makeAddr("solver2");
        vm.deal(solver2, 1 ether);
        deal(address(dai), solver2, 10 * 1e18);
        uint256 solver2UsdcBefore = usdc.balanceOf(solver2);

        vm.startPrank(solver2);
        dai.approve(address(intentGateway), fillPerSolver);
        TokenInfo[] memory out2 = new TokenInfo[](1);
        out2[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: fillPerSolver});
        intentGateway.fillOrder(order, FillOptions({relayerFee: 0, nativeDispatchFee: 0, outputs: out2}));
        vm.stopPrank();

        assertEq(
            usdc.balanceOf(solver2),
            solver2UsdcBefore + truncatedRelease,
            "Solver2 should receive truncated proportional USDC"
        );

        // --- Solver 3 fills final 1 DAI (completes the order) ---
        address solver3 = makeAddr("solver3");
        vm.deal(solver3, 1 ether);
        deal(address(dai), solver3, 10 * 1e18);
        uint256 solver3UsdcBefore = usdc.balanceOf(solver3);

        vm.startPrank(solver3);
        dai.approve(address(intentGateway), fillPerSolver);
        TokenInfo[] memory out3 = new TokenInfo[](1);
        out3[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: fillPerSolver});
        intentGateway.fillOrder(order, FillOptions({relayerFee: 0, nativeDispatchFee: 0, outputs: out3}));
        vm.stopPrank();

        // Final solver should receive the remaining balance (truncatedRelease + 1 rounding unit)
        uint256 expectedFinalRelease = inputAmount - (2 * truncatedRelease); // 33333334
        assertEq(
            usdc.balanceOf(solver3),
            solver3UsdcBefore + expectedFinalRelease,
            "Final solver should receive remaining escrow including rounding dust"
        );
        assertGt(expectedFinalRelease, truncatedRelease, "Final release should be larger due to rounding dust");

        // Gateway should have zero USDC — no dust locked
        assertEq(usdc.balanceOf(address(intentGateway)), 0, "Gateway should have zero USDC - no rounding dust locked");
    }
```
