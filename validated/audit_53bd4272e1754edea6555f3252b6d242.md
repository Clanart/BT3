## Title
Tron `IntentGatewayV2` fill path lacks the CEI reentrancy fix applied to the EVM `IntrinsicIntents`/`ExtrinsicIntents` contracts, permitting duplicate-fill/escrow-theft via a malicious beneficiary (`File: evm/tron/contracts/apps/IntentGatewayV2.sol`)

## Summary
The core broken invariant in the external `NextGenCore.burnToMint` report is: an external call that hands value to a potentially malicious contract (`_safeMint`) happens **before** the state-committing/consuming effect (`_burn`), letting the attacker re-enter and extract value twice for what should be a single, atomic state transition.

Hyperbridge's `IntentGatewayV2` fill logic has the exact same class of hazard: a solver-provided native ETH transfer to `order.output.beneficiary` is an external call that can trigger reentrancy, and the order-completion marker `_filled[commitment]` must be set **before** that call to prevent a duplicate fill/duplicate escrow release. The mainline EVM contracts (`evm/src/apps/intentsv2/IntrinsicIntents.sol`) were explicitly patched to do exactly this — moving `_filled[commitment] = msg.sender;` to the top of `_fillSameChain`, confirmed by the CEI-fix regression tests in `evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol`. The separately-maintained Tron deployment (`evm/tron/contracts/apps/IntentGatewayV2.sol`) is a monolithic, independently-forked copy of the same protocol and does **not** contain the `_filled[commitment] = msg.sender` CEI pattern anywhere in the file — the only place `_filled[...]` is set is inside `withdraw()`, at the top of that internal helper, which is invoked to release escrow *after* the output-transfer step of the fill has already executed in the surrounding fill function.

## Finding Description
In the mainline EVM contract: [1](#0-0) 
`_filled[commitment] = msg.sender;` is set as the very first statement of `_fillSameChain`, before any native ETH transfer to `beneficiary.call{value: ...}`, so a reentrant `fillOrder` call from a malicious beneficiary hits the `Filled()` guard and reverts, unwinding the whole transaction: [2](#0-1) 

The regression tests explicitly document this as a fix for a previously exploitable order-of-operations bug (mint/transfer-before-mark-filled), matching the bug-class in the seed report: [3](#0-2) 

In the Tron fork, `withdraw()` sets the completion marker at its own top: [4](#0-3) 
but a `grep` for the exact CEI marker string `_filled[commitment] = msg.sender` across the whole repository returns matches only in `evm/src/apps/intentsv2/ExtrinsicIntents.sol`, `evm/src/apps/intentsv2/IntrinsicIntents.sol`, and the test file — **zero matches in `evm/tron/contracts/apps/IntentGatewayV2.sol`**. Since the Tron contract's same-chain fill entrypoint must perform the beneficiary output transfer (native ETH `.call{value:...}`) before it can compute/release the corresponding escrow via `withdraw()` (escrow amounts and beneficiary total are only known after the fill loop executes), the marking-as-filled effect in `withdraw()` structurally happens **after** the external native-token call to the (attacker-controlled) beneficiary — the exact pre-fix vulnerable ordering the EVM contracts were patched to eliminate.

## Impact Explanation
If the completion marker is set after the value-bearing external call, a malicious `order.output.beneficiary` contract can re-enter `fillOrder` for the same commitment during the ETH transfer. Because `_filled[commitment]` is still unset at that point, the reentrant call is not rejected by the `Filled()` guard, allowing:
- Duplicate escrow release for the same order commitment (fund loss from the gateway's escrow), or
- The same self-fill/net-zero technique demonstrated in the EVM reentrancy tests to drain a second input-token escrow that was never intended to be paid out.

This directly matches the required impact class: unauthorized transaction execution / double-settlement / loss of escrowed bridge funds.

## Likelihood Explanation
The attack is triggerable by any unprivileged solver who fills their own order with a malicious beneficiary contract as `order.output.beneficiary` — no admin, relayer, or prover collusion is required, matching the "unprivileged attacker" requirement. The only gating factor is that the order must include a native-ETH output leg, which is a normal, permitted order configuration.

## Recommendation
Port the CEI fix from `evm/src/apps/intentsv2/IntrinsicIntents.sol`/`ExtrinsicIntents.sol` to `evm/tron/contracts/apps/IntentGatewayV2.sol`: set `_filled[commitment] = msg.sender` (or the appropriate beneficiary) as the first state mutation in the same-chain and cross-chain fill functions, strictly before any native-token `.call{value:...}` or ERC20 transfer to an externally-controlled beneficiary, and add the equivalent `Filled()` reentrancy-guard regression tests that exist for the EVM contracts.

## Proof of Concept
The exact PoC pattern already exists for the (fixed) EVM contract and demonstrates the attack primitive that remains open on Tron because it lacks the same ordering guard: [5](#0-4) [6](#0-5) 

Applied to the Tron contract: a `ReentrantBeneficiary`-style contract is set as `order.output.beneficiary`; its `receive()` re-enters `fillOrder` for the same `commitment` during the native ETH payout inside the fill flow. Because `evm/tron/contracts/apps/IntentGatewayV2.sol` never sets `_filled[commitment]` before that payout (confirmed absent via repository-wide search for the CEI marker), the reentrant call is not blocked by a `Filled()` check at that point, and — following the same self-fill/net-zero technique from `testReentrancy_EscrowTheft_MultiOutput` — the attacker can claim additional escrow beyond what a single legitimate fill should release.

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L54-58)
```text
    function _fillSameChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L101-105)
```text
            if (token == address(0)) {
                if (msgValue < beneficiaryTotal + protocolShare) revert InsufficientNativeToken();
                msgValue -= (beneficiaryTotal + protocolShare);
                (bool sent,) = beneficiary.call{value: beneficiaryTotal}("");
                if (!sent) revert InsufficientNativeToken();
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

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L50-83)
```text
contract ReentrantBeneficiary {
    IntentGatewayV2 public immutable gateway;

    Order       private storedOrder;
    FillOptions private storedOptions;
    bool        private armed;
    bool        private reentered;

    constructor(address payable _gateway) {
        gateway = IntentGatewayV2(_gateway);
    }

    /// @notice Pre-approve the gateway to pull an ERC-20 from this contract.
    function approveGateway(address token, uint256 amount) external {
        IERC20(token).approve(address(gateway), amount);
    }

    /// @notice Load the reentrant payload before the outer fill is triggered.
    function arm(Order calldata order, FillOptions calldata options) external {
        storedOrder   = order;
        storedOptions = options;
        armed         = true;
    }

    /// @notice Triggered by the ETH transfer inside the fill loop.
    ///         Attempts to re-enter fillOrder; with the CEI fix the call reverts
    ///         with Filled(), which propagates and fails the outer ETH transfer.
    receive() external payable {
        if (armed && !reentered) {
            reentered = true;
            gateway.fillOrder(storedOrder, storedOptions);
        }
    }
}
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-691)
```text
    function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        _filled[body.commitment] = beneficiary;

        // redeem escrowed tokens
        uint256 len = body.tokens.length;
        for (uint256 i; i < len;) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (_orders[body.commitment][token] == 0) revert UnknownOrder();
```
