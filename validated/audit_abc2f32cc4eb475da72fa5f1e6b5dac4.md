## Title
Tron `IntentGatewayV2.placeOrder` lacks the duplicate-input-token guard present in the EVM contract, re-opening the escrow-merging over-release bug — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The external report's core defect is: a counter/balance mutation is computed from an aggregate value that silently combines two logically distinct legs of state, instead of an isolated, per-leg delta — so completing one leg consumes value that belongs to another. The main EVM `IntentGatewayV2.sol` was previously bitten by exactly this class of bug in its same-chain partial-fill escrow accounting and was patched by rejecting duplicate input/output tokens at `placeOrder`. The Tron port of the same contract does not carry this guard.

### Finding Description
In the canonical EVM contract, `placeOrder` explicitly rejects duplicate input tokens before crediting escrow: [1](#0-0) 

This guard exists specifically because `_orders` is keyed only by `(commitment, token)`, not `(commitment, outputIndex)`. Without it, two different order "legs" (input[i] paired with output[i] in the same-chain partial-fill model) that both use the same input token get merged into a single escrow bucket. The regression tests in the EVM test suite spell this out directly: [2](#0-1) [3](#0-2) 

Both tests are labeled as regressions for "same-chain partial fills over-release repeated input escrow" and "prematurely finalize repeated output legs" — confirming this exact bug shape was previously exploitable and had to be fixed with an explicit revert.

The same-chain fill logic that this guard protects reads the *aggregate* `_orders[commitment][token]` balance once a leg's cumulative fill reaches its `totalRequired`, rather than a value scoped to that leg: [4](#0-3) 

If two output legs shared an input token, completing leg A first would read and drain the *combined* balance for that token — including escrow that was supposed to back leg B — handing leg B's collateral to leg A's solver.

The Tron contract (`evm/tron/contracts/apps/IntentGatewayV2.sol`) is a separate, monolithic reimplementation of the same protocol (it does not inherit `IntentsBase`/`IntrinsicIntents`/`ExtrinsicIntents`). Its `placeOrder` computes `reducedInputs` and escrows tokens using additive accumulation, and — unlike the main EVM contract — contains **no check that rejects duplicate input tokens** before crediting escrow: [5](#0-4) 

Both the predispatch and non-predispatch escrow-credit branches use `_orders[commitment][token] += reducedInputs[i].amount;` with no preceding `if (_orders[commitment][token] != 0) revert InvalidInput();` equivalent — the exact safeguard the EVM contract added at line 337 after discovering this bug class.

### Impact Explanation
This falls squarely within the bounty's fund-loss / logic-attack / wrong-beneficiary category. A user or attacker constructing a same-chain order on the Tron gateway with two output legs backed by the same input token could have one leg's completion drain escrow value that was earmarked to pay for the other leg's fill. Depending on fill ordering, this can result in: a solver receiving escrowed input tokens far in excess of what they actually delivered output for (fund theft from the order placer/other solver), or the second leg reverting/being permanently unfillable while its backing collateral has already left escrow (fund lock/loss for the order's beneficiary). This is a direct analog to the "self-transfer decrements shared counter and unlocks an inconsistent, exploitable state" pattern in the seed report — here the shared, un-partitioned `_orders[commitment][token]` mapping plays the role of `totalInvestors`.

### Likelihood Explanation
Any unprivileged user can call `placeOrder` with two entries in `order.inputs` referencing the same ERC-20 (or two native-token entries) — the Tron contract has no revert to stop this, and both loops in `placeOrder` simply add to the shared bucket. No relayer, prover, or admin cooperation is needed; only a same-chain fill sequence is required to trigger the merge-then-release. The EVM codebase's own test suite proves this scenario is realistic and was hit in production-track development.

### Recommendation
Port the exact fix already present in `evm/src/apps/IntentGatewayV2.sol` to the Tron contract: before/while crediting `_orders[commitment][token]`, revert with `InvalidInput()` if that (commitment, token) slot has already been written for this order (i.e., reject duplicate input tokens), and add the equivalent transient-storage duplicate-output-token check used in the main contract's `placeOrder` (lines 165–189) if the Tron contract supports the same-chain partial-fill flow with multiple output legs.

### Proof of Concept
Conceptual PoC (mirrors the EVM regression test, adapted for the Tron contract's lack of a guard):
1. Attacker calls `IntentGatewayV2(tron).placeOrder(order, graffiti)` where `order.inputs = [{token: USDC, amount: 1200}, {token: USDC, amount: 1000}]` and `order.output.assets = [{token: DAI, amount: 500e18}, {token: DAI, amount: 1000e18}]`.
2. Because there is no duplicate-input check, `_orders[commitment][USDC]` accumulates to `2200` (the merged sum of both legs) instead of being rejected.
3. A solver fills leg 0 (500 DAI) to completion; the same-chain fill logic (mirroring `IntrinsicIntents._fillSameChain`, lines 116-122) reads the current aggregate `_orders[commitment][USDC]` balance to compute the "final" escrow release for that leg and pays out the full `2200` USDC — draining collateral that should have remained to back leg 1's 1000 DAI fill.
4. Leg 1 is now unfillable (its escrow is gone), or a colluding solver can claim it a second time depending on internal bookkeeping — either fund theft or fund lock results.

Note: I was able to fully confirm (a) the missing duplicate-input-token guard in the Tron `placeOrder` and (b) the exact aggregate-balance-release pattern in the shared `IntrinsicIntents.sol` logic used by the main EVM contract, plus explicit regression tests proving this bug class was real and exploitable pre-fix. I was not able to read the Tron contract's own same-chain fill/`withdraw` function body in this session (only `placeOrder` and the cross-chain `withdraw` at lines 661-735 were retrieved), so I cannot cite the Tron file's exact escrow-release expression byte-for-byte — this should be verified by a follow-up read of the Tron contract's fill-order function before remediation.

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

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L1931-1938)
```text
    /// @notice Placing an order with duplicate input tokens must revert.
    /// Regression test for: same-chain partial fills over-release repeated input escrow.
    function testRevert_PlaceOrder_DuplicateInputTokens() public {
        // Two input legs both using USDC — this previously merged into one escrow bucket
        TokenInfo[] memory inputs = new TokenInfo[](2);
        inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: 1200 * 1e6});
        inputs[1] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: 1000 * 1e6});

```

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L2054-2064)
```text
    /// @notice Placing an order with duplicate output tokens must revert.
    /// Regression test for: same-chain partial fills prematurely finalize repeated output legs.
    function testRevert_PlaceOrder_DuplicateOutputTokens() public {
        TokenInfo[] memory inputs = new TokenInfo[](2);
        inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: 1000 * 1e6});
        inputs[1] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 500 * 1e18});

        // Two output legs both requesting DAI — shares one _partialFills bucket
        TokenInfo[] memory outputAssets = new TokenInfo[](2);
        outputAssets[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 400 * 1e18});
        outputAssets[1] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 600 * 1e18});
```

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L433-463)
```text

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }

            // Execute transfer calls from call dispatcher
            ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls));
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
        }
```
