Based on my investigation, I was unable to fully confirm whether `fillOrder` itself carries a `nonReentrant` modifier (only `placeOrder` was confirmed to have one at [1](#0-0) ). However, the dedicated test suite `IntrinsicIntentsReentrancyTest.sol` explicitly documents that reentrancy protection for `fillOrder` relies entirely on the "CEI fix" — setting `_filled[commitment] = msg.sender` before any external call — rather than a `ReentrancyGuard` modifier [2](#0-1) . This confirms the guard is purely the `_filled` mapping state, which is exactly what gets reset mid-function on partial fills.

### Title
Reentrancy guard reset before trailing native refund in `_fillSameChain` allows same-order re-entrant partial fills - (File: evm/src/apps/intentsv2/IntrinsicIntents.sol)

### Summary
`IntrinsicIntents._fillSameChain` uses `_filled[commitment]` as its sole reentrancy guard, set at the top of the function per the documented CEI fix. On a **partial** fill, the function explicitly `delete`s this guard (`delete _filled[commitment];`) before the function's own trailing external call — the native ETH refund of unspent `msgValue` to `msg.sender` [3](#0-2) . This mirrors the dittoeth pattern: a "close/finalize" step (there, `deleteShortRecord`; here, `delete _filled[commitment]`) clears a guard/identifier that a *later* step in the same logical flow still assumes to be intact, letting an untrusted party re-enter and reuse the freshly-cleared slot before the outer call's bookkeeping (partial-fill accounting, surplus/dust splitting) has fully settled.

### Finding Description
`_filled[commitment] = msg.sender;` is set at function entry specifically to block reentrancy during the per-output ETH transfers to the beneficiary inside the loop [4](#0-3) . That protects the beneficiary-facing transfers, which is what the existing `IntrinsicIntentsReentrancyTest.sol` suite tests (`testReentrancy_FeeTheft`, `testReentrancy_EscrowTheft_MultiOutput`) [5](#0-4) .

However, on the non-full-fill branch, the guard is torn down again inside the same call, before the function performs its own external call: [3](#0-2) 

If `order.output.assets` includes a native-ETH leg and the solver (`msg.sender`) overpays `msg.value` relative to what this single call consumes, the leftover `msgValue` is sent back to `msg.sender` via a raw `.call{value: msgValue}("")` *after* `_filled[commitment]` has already been deleted for the partial-fill path. If `msg.sender` is a contract, its `receive()`/fallback executes with `_filled[commitment] == address(0)` — the exact state the entry check treats as "not yet filled" — so a nested call into `fillOrder` for the **same commitment** is not rejected by `Filled()`.

This is structurally identical to the dittoeth bug: the "finalize" operation (partial fill) tears down the very state (`_filled[commitment]`) that other code relies on as a single-owner/one-shot marker, and an attacker-controlled external call (the refund, played by a malicious solver contract instead of a malicious beneficiary) is timed to land exactly in that reopened window.

### Impact Explanation
A malicious solver contract could recursively re-enter `fillOrder` on the same partially-filled order commitment within one transaction. Because `_partialFills[commitment][outputToken]` and `_orders[commitment][token]` are only read/written per call (not atomically fenced against nested calls beyond the `_filled` guard, which is exactly the value being cleared), nested fills could compute `remaining`/`fillAmount`/surplus splits against state that the outer call has not finished reconciling, potentially letting the solver claim escrowed input tokens disproportionate to output tokens actually delivered, or repeatedly trigger the surplus/dust-splitting logic. This falls under "transaction manipulation" / "unauthorized execution" / possible "wrong beneficiary or amount" release of escrowed bridge-adjacent funds, matching the bounty's fund-loss and logic-attack categories.

### Likelihood Explanation
Requires: (1) an order with a native-ETH output leg, (2) a solver willing to overpay `msg.value` so a nonzero refund is triggered, and (3) the solver being a contract that reenters on `receive()`. All three conditions are fully attacker-controlled — no relayer, prover, or admin cooperation is needed, and the existing reentrancy test suite explicitly does not cover this particular window (it only arms reentrancy from the beneficiary-transfer call inside the loop, not from the solver-refund call after the partial-fill `delete`). This makes it a plausible, low-effort attacker path once contract-based solvers exist.

### Recommendation
Do not clear `_filled[commitment]` before completing all external calls in `_fillSameChain`. Move the `delete _filled[commitment]` (and the `PartialFill` bookkeeping) to occur strictly after the trailing `msgValue` refund, or move the refund earlier so no external call follows the guard's removal — preserving checks-effects-interactions for the *entire* function body, not just the loop.

### Proof of Concept
Exact reproduction was not verified against a live fork/test run within this session, so the following is a structural PoC sketch based on the code paths cited above (this should be validated with a Foundry test before treating it as confirmed exploitable):
1. Deploy a malicious solver contract that implements `receive()` to call `intentGateway.fillOrder(sameOrder, ...)` once, mirroring `ReentrantBeneficiary` in `IntrinsicIntentsReentrancyTest.sol` but attached to the *solver* role instead of the beneficiary.
2. Place a same-chain order whose output includes a native-ETH asset with `totalRequired` less than the solver amount supplied, ensuring the fill is partial (e.g., cap `solverAmount` at `remaining` so `isFullyFilled=false`) while `msg.value` sent exceeds what the loop consumes.
3. Call `fillOrder` from the malicious solver contract; the trailing `msgValue` refund at [6](#0-5)  fires while `_filled[commitment]` is already `address(0)` (cleared at line 140), letting the `receive()` hook re-enter `fillOrder` for the same commitment.
4. Compare escrow released (`_orders[commitment][token]`) and output tokens actually delivered across both the outer and nested calls to confirm whether the solver extracted more input-token escrow than the output tokens it supplied.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L162-162)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable nonReentrant {
```

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L37-49)
```text
 * Attack window (pre-fix):
 *
 *   _fillSameChain / _fillCrossChain:
 *     beneficiary.call{value: ...}("")   ← RE-ENTRY HERE
 *     // _filled still == address(0) pre-fix, now set at the top (CEI)
 *
 * With the CEI fix in place, `_filled[commitment]` is set to `msg.sender` at the
 * very start of both fill functions. Any reentrant `fillOrder` call therefore hits
 * the `if (_filled[commitment] != address(0)) revert Filled()` guard and reverts.
 * That revert propagates through `receive()`, causing the outer ETH transfer to
 * return `(false, ...)`, which triggers `InsufficientNativeToken()` in the outer
 * call — rolling back all state changes atomically.
 */
```

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L228-303)
```text
    function testReentrancy_FeeTheft() public {
        // ── 1. Place a same-chain order (input=USDC, output=ETH, fees=DAI) ───

        TokenInfo[] memory inputs = new TokenInfo[](1);
        inputs[0] = TokenInfo({
            token:  bytes32(uint256(uint160(address(usdc)))),
            amount: INPUT_USDC
        });

        TokenInfo[] memory outputAssets = new TokenInfo[](1);
        outputAssets[0] = TokenInfo({token: bytes32(0), amount: OUTPUT_ETH});

        Order memory order = _sameChainOrder(inputs, outputAssets, TX_FEES);

        vm.startPrank(attacker);
        usdc.approve(address(intentGateway), INPUT_USDC);
        dai.approve(address(intentGateway), TX_FEES);
        intentGateway.placeOrder(order, bytes32(0));
        vm.stopPrank();

        // Reconstruct the stamped order for commitment computation.
        order.user   = bytes32(uint256(uint160(attacker)));
        order.source = host.host();
        order.nonce  = 0;

        bytes32 commitment = keccak256(abi.encode(order));

        // Sanity: confirm fees are escrowed.
        assertEq(intentGateway._orders(commitment, TRANSACTION_FEES), TX_FEES);

        // ── 2. Arm the malicious beneficiary ─────────────────────────────────
        //
        // The reentrant FillOptions passes amount=0 so the re-entered loop's
        // `remaining == 0 || solverAmount == 0` branch is taken — but this
        // code path is never reached because _filled[commitment] is already set.

        TokenInfo[] memory reentrantOutputs = new TokenInfo[](1);
        reentrantOutputs[0] = TokenInfo({token: bytes32(0), amount: 0});

        maliciousBeneficiary.arm(
            order,
            FillOptions({relayerFee: 0, nativeDispatchFee: 0, outputs: reentrantOutputs})
        );

        // ── 3. Fill attempt reverts — reentrancy is blocked ──────────────────

        vm.expectRevert(ERR_INSUFFICIENT_NATIVE);
        vm.prank(legitimateSolver);
        intentGateway.fillOrder{value: OUTPUT_ETH}(
            order,
            FillOptions({relayerFee: 0, nativeDispatchFee: 0, outputs: outputAssets})
        );

        // ── 4. State is completely rolled back ───────────────────────────────

        assertEq(
            intentGateway._orders(commitment, TRANSACTION_FEES),
            TX_FEES,
            "fees must still be escrowed after revert"
        );
        assertEq(
            intentGateway._filled(commitment),
            address(0),
            "order must not be marked filled after revert"
        );
        assertEq(
            dai.balanceOf(address(maliciousBeneficiary)),
            0,
            "malicious beneficiary must not receive stolen fees"
        );
        assertEq(
            usdc.balanceOf(legitimateSolver),
            0,
            "solver must not have received any escrow"
        );
    }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L54-60)
```text
    function _fillSameChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L136-148)
```text
        if (isFullyFilled) {
            _execute(order, outputsLen);
            emit OrderFilled({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
        } else {
            delete _filled[commitment];
            emit PartialFill({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
        }

        // Refund any unspent native tokens to the solver.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }
```
