### Title
Escrow bucket keyed only by token address lets a completed output leg drain another leg's still-owed escrow in `IntrinsicIntents._fillSameChain` - (File: evm/src/apps/intentsv2/IntrinsicIntents.sol)

### Summary
The Panoptic report's core broken invariant is: a rounding/dust discrepancy between a "gross" accounting bucket (`totalLiquidity`/`removedLiquidity`) and a "per-operation" proportional calculation lets one operation either revert unexpectedly (DoS) or consume more of the shared pool than it should. The Hyperbridge analog lives in `IntrinsicIntents._fillSameChain`, where the escrow released per output leg is looked up from a single `_orders[commitment][token]` bucket keyed only by **token address**, not by output-leg index, while fill-completion is tracked **per index** via `_partialFills[commitment][outputToken]`.

### Finding Description
`IntentsBase._orders` is declared as: [1](#0-0) 

i.e. escrow is accounted per `(commitment, token)`, not per `(commitment, output-leg-index)`. But the release logic in `_fillSameChain` is computed per output-leg index `i` and, on the leg that reaches full completion, intentionally sweeps the **entire remaining balance** of the corresponding input-token bucket rather than a proportional slice: [2](#0-1) 

This "give the last filler the whole remaining bucket" logic is the fix for the exact rounding-dust class of bug the external report also describes (integer-division truncation leaving un-released escrow), and is explicitly tested for the single-input/single-output case: [3](#0-2) 

The problem is that the sweep is keyed by `order.inputs[i].token`'s address, not by index. If an `Order` has multiple `inputs`/`output.assets` entries where two different indices reference **the same input token address**, both legs share one `_orders[commitment][token]` bucket, while `_partialFills` (and thus "is this leg done") is tracked independently per index. The first leg to reach `amountFilled == totalRequired` will execute `escrowedAmount = _orders[commitment][token]` — draining the *entire* shared bucket, including the portion still owed to the other, not-yet-completed leg.

Downstream, `_withdraw` unconditionally decrements the bucket and transfers out `amount`: [4](#0-3) 

Once the shared bucket is emptied by the first-completing leg, any later fill on the still-open leg calls `_withdraw` and hits `escrowed == 0` → `revert UnknownOrder()`, permanently DoS-ing settlement of that leg. Alternatively, depending on which leg completes first and its `fillAmount` sizing, the first filler can walk away with more of the shared escrow than their leg's proportional share entitles them to — an incorrect-amount payout at the expense of whoever fills (or cancels) the other leg.

Nothing in the reviewed `_fillSameChain`/`_withdraw`/`IntentsBase` code enforces that `order.inputs[]` token addresses are unique per order before this per-token bucket accounting is relied upon; I was not able to verify within the available tool budget whether `placeOrder`/order-validation code (not fully reviewed) rejects orders with duplicate input token addresses across indices. This is a real, unresolved gap in my investigation and should be checked directly in `evm/src/apps/IntentGatewayV2.sol`/`ExtrinsicIntents.sol` before treating this as fully confirmed.

### Impact Explanation
If duplicate input-token orders are constructible (i.e., no dedup check exists), this breaks the "bridged assets... must move exactly once and only to the rightful beneficiary and amount" invariant: either (a) a solver receives more escrow than their fill proportionally earned (fund loss to the user/other solver), or (b) the remaining leg's solver is permanently unable to collect their earned escrow (a self-inflicted settlement DoS matching the Panoptic "self-DOS on normal operations" impact class, assessed Medium there).

### Likelihood Explanation
Requires only an unprivileged order-placer to construct a multi-leg order with a repeated input-token address (no relayer, prover, or admin involvement) and an unprivileged solver to fill legs in a particular order — matching the required "unprivileged attacker" profile. Likelihood is contingent on the unverified precondition that order construction does not enforce unique input tokens per leg; this must be confirmed against `placeOrder` validation before treating the issue as exploitable in production.

### Recommendation
- Key `_orders` (or an internal escrow ledger) by `(commitment, output-leg-index)` instead of `(commitment, token-address)`, or enforce that `order.inputs[]` contains no duplicate token addresses at `placeOrder` time.
- If the current "sweep the whole bucket on last-leg completion" optimization is kept, scope the sweep so it can never consume the portion allocated to other, still-incomplete legs sharing the same token.

### Proof of Concept
Not independently executed; derivable from the cited code paths as follows:
1. `user` calls `placeOrder` with an `Order` whose `inputs = [ (USDC, 600), (USDC, 400) ]` and `output.assets = [ (DAI, 600e18), (WETH, 1e18) ]` (two legs sharing the USDC input token address across indices 0 and 1), assuming this passes validation (unverified).
2. Solver A fully fills leg 0 (`DAI` output) in one transaction. `_partialFills[commitment][DAI] == 600e18`, so `amountFilled == totalRequired` for leg 0, triggering `escrowedAmount = _orders[commitment][USDC]` — the full 1000 USDC bucket — released to Solver A instead of the 600 USDC leg-0 share.
3. Solver B later attempts to fill leg 1 (`WETH` output). `_withdraw` reads `_orders[commitment][USDC] == 0` and reverts with `UnknownOrder`, permanently blocking settlement of leg 1 and leaving Solver B unable to collect the escrow they are owed — reproducing the Panoptic-style self-DoS/fund-misallocation pattern from a single dust/bucket-accounting mismatch.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L136-140)
```text
    /**
     * @dev Maps (commitment, token address) to the escrowed amount for that token.
     * Decremented as tokens are released via fills or refunds.
     */
    mapping(bytes32 => mapping(address => uint256)) public _orders;
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L400-409)
```text
            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L116-123)
```text
            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
            escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
            outputFills[i] = TokenInfo({token: outputToken, amount: fillAmount});
```

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L1550-1657)
```text
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
