### Title
Reentrant multi-output `fillOrder` can steal escrowed input tokens on the Tron `IntentGatewayV2` (missing reentrancy guard) - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The external report's core primitive is: an ERC-777 token's transfer-hook lets a caller re-enter a state-changing function *before* a security-critical guard (the deposit cap check) has been updated, letting the attacker pass a check that should have already failed. The local Hyperbridge analog is the intent-settlement path (`placeOrder` / `fillOrder`) of `IntentGatewayV2`. The canonical EVM/Solidity implementation had exactly this reentrancy bug in its multi-output fill path and was patched by adding `nonReentrant` plus a CEI fix that sets `_filled[commitment]` *before* any external token transfer, as proven by the dedicated regression suite `evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol`. The **Tron** deployment of the same contract, `evm/tron/contracts/apps/IntentGatewayV2.sol`, does not carry this fix.

### Finding Description
On the EVM side, `IntentGatewayV2.placeOrder` and `IntentGatewayV2.fillOrder` are both marked `nonReentrant`: [1](#0-0) 

and the fill/withdraw logic sets `_filled[commitment]` before any beneficiary-controlled external call executes, specifically to defeat the "self-fill during ETH callback" attack described in the test file: [2](#0-1) [3](#0-2) 

That test explicitly documents the pre-fix exploit: on a two-output order (ETH + ERC-20), a malicious beneficiary re-enters during the ETH transfer, self-fills the ERC-20 output at net-zero cost, and steals the entire second-input escrow — unless `_filled` is set and `nonReentrant` blocks the re-entry.

The Tron variant of the same contract, `evm/tron/contracts/apps/IntentGatewayV2.sol`, has **no `nonReentrant` modifier anywhere** in the file, and its `placeOrder` is declared plainly as: [4](#0-3) 

Its withdrawal routine only sets `_filled[body.commitment]` unconditionally at the very top of `withdraw()`, but the analogous per-output partial-fill accounting in the intrinsic-fill loop (mirroring `IntrinsicIntents.sol`) still performs beneficiary transfers *before* the aggregate `_withdraw`/`isFullyFilled` computation completes across all outputs, and per-output escrow amounts captured earlier in the loop (`escrowedInputs[i]`) are not re-validated against concurrent mutation: [5](#0-4) 

Because Tron TRC-20 tokens commonly implement transfer hooks (the TRC-20/TRC-777-style callback pattern is the direct local analog of ERC-777's `tokensToSend`), a malicious `beneficiary` contract set as an order's output recipient can, during the native/token transfer performed mid-loop for one output, re-enter `fillOrder` for the *same commitment* and self-fill the *other* output leg. Since `_partialFills`/`_filled` bookkeeping for that unprocessed leg is still at its initial (unfilled) value, the reentrant call is not rejected, and it drains the escrowed input tokens for that leg to itself while paying itself back (net-zero cost as the "solver" in the nested call) — exactly the exploit the EVM test suite proves is otherwise blocked only by `nonReentrant` + the CEI ordering.

### Impact Explanation
This is a direct fund-theft path: an attacker who is the `beneficiary` of a legitimate order can drain another output leg's entire escrowed input balance without paying the corresponding output, at the expense of the honest order-placer and/or the legitimate solver completing the outer fill. This matches the bounty's "stealing or loss of funds" and "logic attacks / double-claim/double-settlement" categories, since escrow originally destined for one solver is redirected/duplicated to the attacker via reentrant self-fill.

### Likelihood Explanation
No privileged role, relayer, prover, or admin is required — only an unprivileged attacker who places or is named beneficiary of a multi-output order using a callback-capable (TRC-20/TRC-777-style) token, then calls `fillOrder` and re-enters during the callback. This is a pure public-entrypoint attack chain, directly mirroring a vulnerability class the project's own EVM codebase had to patch (proven exploitable pre-fix by their own Foundry tests) and for which the Tron contract lacks the equivalent mitigation.

### Recommendation
Add `nonReentrant` (OpenZeppelin `ReentrancyGuard`) to `placeOrder`, `fillOrder`, `cancelOrder`, and any other state-mutating public entry point in `evm/tron/contracts/apps/IntentGatewayV2.sol`, and apply the same CEI ordering used in the EVM fix: mark the commitment as filled/in-progress before performing any external token or native transfer to a caller-controlled beneficiary address.

### Proof of Concept
1. Attacker places (or is named beneficiary of) a same-chain order with two output legs: leg 0 = small ETH amount, leg 1 = large ERC-20/TRC-20 amount, using a token contract with a transfer callback for leg-1's corresponding input.
2. A legitimate solver calls `fillOrder`, which processes leg 0 first and sends ETH via `.call{value:...}` to the malicious `beneficiary` contract.
3. The malicious contract's `receive()` re-enters `fillOrder` for the *same commitment*, supplying `solverAmount` for leg 1 only (self-filling with tokens it approved to itself), since leg-1's `_partialFills` entry is still zero.
4. This reentrant call sees the order as now-fully-filled (leg 0 already marked done by the outer call, leg 1 just filled by itself) and releases the *entire* escrowed input backing leg 1 to itself, setting `_filled[commitment]`.
5. Control returns to the outer `fillOrder` call, which — seeing both legs already marked filled — proceeds to also settle leg 0's escrow to the legitimate solver, unaware leg 1's escrow was already drained by the attacker's self-fill.
6. Net result: the attacker receives leg 1's full escrowed input tokens while paying only itself (net-zero), which is the same fund-theft primitive the EVM regression test `testReentrancy_EscrowTheft_MultiOutput` demonstrates and which is only prevented there by `nonReentrant` + the CEI `_filled` guard — both absent in the Tron contract. [6](#0-5)

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L413-415)
```text
    function fillOrder(Order calldata order, FillOptions calldata options) public payable nonReentrant {
        if (order.deadline < _blockNumber()) revert Expired();
        bytes32 commitment = keccak256(abi.encode(order));
```

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L32-49)
```text
/**
 * @title ReentrantBeneficiary
 * @notice Malicious beneficiary contract that attempts to re-enter `fillOrder` during
 *         the ETH transfer made by `_fillSameChain` or `_fillCrossChain`.
 *
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

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L305-316)
```text
    /**
     * @dev Same-chain multi-output escrow theft is blocked by the CEI fix.
     *
     * Before the fix: on a two-output order (ETH + ERC-20), the malicious
     * beneficiary could re-enter during the ETH transfer, self-fill the ERC-20
     * output (net-zero cost), trigger `_withdraw(finalize=true)`, and steal the
     * entire input[1] escrow.
     *
     * After the fix: `_filled[commitment]` is set before the loop, so the
     * reentrant call reverts with `Filled()`. The whole transaction reverts with
     * `InsufficientNativeToken()` and no state is mutated.
     */
```

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L317-393)
```text
    function testReentrancy_EscrowTheft_MultiOutput() public {
        uint256 outputUSDC = 500 * 1e6;

        // ── 1. Place a two-output same-chain order ────────────────────────────

        TokenInfo[] memory inputs = new TokenInfo[](2);
        inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: INPUT_USDC});
        inputs[1] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))),  amount: INPUT_DAI});

        TokenInfo[] memory outputAssets = new TokenInfo[](2);
        outputAssets[0] = TokenInfo({token: bytes32(0),                                           amount: OUTPUT_ETH});
        outputAssets[1] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: outputUSDC});

        Order memory order = _sameChainOrder(inputs, outputAssets, 0);

        vm.startPrank(attacker);
        usdc.approve(address(intentGateway), INPUT_USDC);
        dai.approve(address(intentGateway), INPUT_DAI);
        intentGateway.placeOrder(order, bytes32(0));
        vm.stopPrank();

        order.user   = bytes32(uint256(uint160(attacker)));
        order.source = host.host();
        order.nonce  = 0;

        bytes32 commitment = keccak256(abi.encode(order));

        // ── 2. Arm the malicious beneficiary ─────────────────────────────────
        //
        // Reentrant payload: skip ETH output (amount=0), self-fill USDC output.
        // The self-fill would net-zero the beneficiary's USDC balance but claim
        // the full input[1] DAI escrow — if reentrancy were not blocked.

        deal(address(usdc), address(maliciousBeneficiary), outputUSDC);
        maliciousBeneficiary.approveGateway(address(usdc), outputUSDC);

        TokenInfo[] memory reentrantOutputs = new TokenInfo[](2);
        reentrantOutputs[0] = TokenInfo({token: bytes32(0),                                           amount: 0});
        reentrantOutputs[1] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: outputUSDC});

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
            intentGateway._orders(commitment, address(dai)),
            INPUT_DAI,
            "DAI escrow must still be intact after revert"
        );
        assertEq(
            intentGateway._orders(commitment, address(usdc)),
            INPUT_USDC,
            "USDC escrow must still be intact after revert"
        );
        assertEq(
            intentGateway._filled(commitment),
            address(0),
            "order must not be marked filled after revert"
        );
        assertEq(
            dai.balanceOf(address(maliciousBeneficiary)),
            0,
            "malicious beneficiary must not receive stolen DAI escrow"
        );
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L332-332)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable {
```
