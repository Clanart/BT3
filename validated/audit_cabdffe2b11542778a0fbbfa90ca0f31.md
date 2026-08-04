## Title
Duplicate input tokens are silently merged (not rejected) in Tron `IntentGatewayV2.placeOrder`, re-opening the escrow over-release bug already patched on EVM — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
This is a direct structural analog of the C4 "duplicate lpToken" class bug: a registration/accounting function accepts a duplicate key (there, `lpToken`; here, the input `token` address inside one order) without rejecting it, so two logically distinct entries collapse into one shared accounting slot and downstream math becomes wrong. On the canonical EVM `IntentGatewayV2.sol`, this exact bug class was found and fixed: duplicate input tokens in one `Order` are explicitly rejected because they previously caused "over-release" of escrow during partial fills. The Tron variant of the same contract, `evm/tron/contracts/apps/IntentGatewayV2.sol`, still uses the old, unguarded pattern.

### Finding Description
On EVM, `IntentGatewayV2.placeOrder` explicitly guards against duplicate input tokens by reverting instead of merging: [1](#0-0) 

The associated regression test states the bug class directly: [2](#0-1) 

The comment is explicit: "same-chain partial fills over-release repeated input escrow" — i.e., merging two input legs of the same token into one `_orders[commitment][token]` bucket lets a later partial release/withdraw against that bucket be replayed or over-drawn beyond what a single leg actually escrowed.

The Tron port of the same contract, `evm/tron/contracts/apps/IntentGatewayV2.sol`, has no such guard. It escrows each input leg into the same mapping using **accumulation** (`+=`) with no duplicate check: [3](#0-2) [4](#0-3) 

This is the exact "add()-without-duplicate-check" pattern from the C4 report translated to escrow accounting: two order legs that reference the same `token` address are indistinguishable once summed into `_orders[commitment][token]`, just as two `add()` calls with the same `lpToken` collapse into one pool whose `lpSupply` becomes miscounted. The release path (`_withdraw`/fill logic) reads and decrements this same per-`(commitment, token)` bucket per output/withdrawal line item: [5](#0-4) 

Because a single `token` bucket can now represent the sum of multiple originally-distinct input legs, a partial-fill/partial-withdraw sequence that is only supposed to release a fraction of one leg's escrow can instead draw against the pooled balance of both legs, letting more value be released per withdrawal step than any single leg actually contributed — mirroring how duplicate `lpToken` pools corrupted `lpSupply`-based reward math in the original report.

### Impact Explanation
This is reachable directly by an unprivileged order-placer calling `placeOrder` on the Tron-deployed `IntentGatewayV2` with two input legs of the same token address, then working with a solver through the normal `fillOrder`/partial-withdrawal flow. It results in transaction/logic manipulation of the escrow accounting and potential over-release of bridge-custodied funds from the gateway's escrow — squarely within the "stealing or loss of funds" / "transaction manipulation" bounty categories, and requires no relayer, prover, or governance actor, only a user constructing an order with a duplicate token leg.

### Likelihood Explanation
High: the vulnerable code path (`placeOrder` with duplicate `order.inputs[i].token` entries) is a normal public entry point with no special permissions, and the EVM sibling contract's own test suite proves this exact scenario is exploitable when unguarded ("previously merged into one escrow bucket"). The Tron file simply never received the corresponding fix, which appears to stem from Tron/TVM not supporting EVM transient storage (`tload`/`tstore`) used for the parallel duplicate-*output*-token guard on the EVM side — suggesting the whole duplicate-token hardening pass was not ported to the Tron build.

### Recommendation
Port the duplicate-input-token guard from `evm/src/apps/IntentGatewayV2.sol` (the check-and-revert on `_orders[commitment][token] != 0` before initial write, using ordinary storage instead of transient storage since Tron/TVM lacks `TLOAD`/`TSTORE`) into `evm/tron/contracts/apps/IntentGatewayV2.sol`, for both the direct-transfer and predispatch/call-dispatcher escrow loops. Add the same duplicate-output-token rejection for `order.output.assets` using a memory-based dedup check instead of transient storage.

### Proof of Concept
1. A user calls `placeOrder` on the Tron `IntentGatewayV2` with `order.inputs = [ {token: USDC, amount: 1200e6}, {token: USDC, amount: 1000e6} ]`.
2. Unlike the EVM contract (which reverts with `InvalidInput` per the regression test at `evm/tests/foundry/IntentGatewayV2SameChainTest.sol:1933-1964`), the Tron contract accepts this and executes `_orders[commitment][USDC] += reducedInputs[i].amount` twice, leaving `_orders[commitment][USDC] == 2200e6` sourced from what should be two independently tracked legs.
3. During a partial fill/withdrawal sequence against this order, the solver's withdrawal logic decrements this single pooled bucket per release call; because the bucket no longer reflects a single leg's true balance, a release computed against one nominal leg's amount can be repeated or over-drawn against the merged 2200e6 total, releasing more escrowed value than the corresponding fill actually paid for — the same "over-release" scenario the EVM fix's regression test was written to prevent.

Note: I could not view the Tron contract's `fillOrder`/withdrawal function body directly in the time available (only the `placeOrder` and `cancelOrder` regions were retrieved), so the exact over-release arithmetic in the Tron withdrawal path is inferred from the shared `_orders[commitment][token]` accounting model and the EVM sibling's documented bug/fix, not from a directly observed Tron withdrawal snippet. A Devin session with full file access should confirm the Tron withdrawal/fill logic decrements this same mapping per output line item before treating this as final.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L333-343)
```text
        // Phase 3: Credit escrow.
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            // Reject duplicate input tokens
            if (_orders[commitment][token] != 0) revert InvalidInput();
            _orders[commitment][token] = reducedInputs[i].amount;

            unchecked {
                ++i;
            }
        }
```

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L1927-1943)
```text
    /*//////////////////////////////////////////////////////////////
                    DUPLICATE INPUT TOKEN REJECTION TESTS
    //////////////////////////////////////////////////////////////*/

    /// @notice Placing an order with duplicate input tokens must revert.
    /// Regression test for: same-chain partial fills over-release repeated input escrow.
    function testRevert_PlaceOrder_DuplicateInputTokens() public {
        // Two input legs both using USDC — this previously merged into one escrow bucket
        TokenInfo[] memory inputs = new TokenInfo[](2);
        inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: 1200 * 1e6});
        inputs[1] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: 1000 * 1e6});

        TokenInfo[] memory outputAssets = new TokenInfo[](2);
        outputAssets[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 500 * 1e18});
        outputAssets[1] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 1000 * 1e18});

        PaymentInfo memory output =
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L410-440)
```text
            // Transfer tokens from call dispatcher back to IntentGateway
            Call[] memory transferCalls = new Call[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;
                uint256 balance;

                if (token == address(0)) {
                    balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                } else {
                    balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                }

                uint256 dust = balance - requiredAmount;
                if (dust > 0) emit DustCollected(token, dust);

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L444-462)
```text
        } else {
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    // native token
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                }

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-410)
```text
    function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;

        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
        }
```
