### Title
Tron `IntentGatewayV2.placeOrder` drops the duplicate-input-token guard, letting one escrow bucket back multiple order legs - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The mainline EVM intent gateway explicitly guards against multiple `order.inputs` entries pointing at the same token, because `_orders[commitment][token]` is a single aggregate value keyed only by `(commitment, token)`, not by input index. The Tron variant of the same contract drops that guard and instead accumulates into the shared bucket with `+=`, reproducing exactly the "complicated state update" pattern from the seed report: a value that must track several logically distinct legs is instead maintained as one aggregate counter, and the call sites that later drain it were written assuming one-input-per-token.

### Finding Description
In the reference implementation, `placeOrder` rejects duplicate input tokens before crediting escrow: [1](#0-0) 

This makes the invariant "`_orders[commitment][token]` represents exactly one input leg" hold for every downstream consumer, including the per-leg "grab full remaining balance" release logic in `_fillSameChain`: [2](#0-1) 

The Tron port of `IntentGatewayV2.placeOrder`, however, has no equivalent duplicate-token check and instead sums straight into the same storage slot on every loop iteration: [3](#0-2) [4](#0-3) 

Both the predispatch branch (`_orders[commitment][token] += reducedInputs[i].amount;`) and the direct-transfer branch use `+=` unconditionally, so an order with two (or more) `order.inputs` entries for the same token address silently merges their escrow into one bucket instead of reverting like the mainline contract does. `withdraw()` in the same file only guards on the aggregate being non-zero, not on it correctly corresponding to a single logical leg: [5](#0-4) 

This is the same bug class as the seed report: a state variable that needs coordinated, complete, and complementary updates across multiple call sites (`placeOrder`'s per-leg credit and `withdraw`'s per-leg debit) is instead treated as one flat counter in one implementation while every other code path that touches it (including the counterpart mainline contract's own `_fillSameChain`) was written and reviewed assuming a strict one-token-per-leg mapping.

### Impact Explanation
An order placed through the Tron gateway with a duplicated input token collapses two independently-priced/quoted legs into a single escrow slot. Any code path elsewhere in the intents pipeline that assumes `_orders[commitment][token]` corresponds to exactly one `order.inputs[i]` entry (as the mainline `_fillSameChain`/`_withdraw` logic does) can then release or account for more or less than the amount actually owed for a given leg, resulting in a wrong-amount payout to whichever party's leg is settled first and permanent lock/loss of the escrow backing the other leg. This falls squarely under the bounty's "wrong beneficiary or amount" and "loss/lock of funds" categories and requires no privileged actor, relayer, or malicious peer — only a user placing an order with a duplicated input token, which the mainline contract explicitly treats as invalid input but the Tron contract silently accepts.

### Likelihood Explanation
Reachable by any unprivileged user calling `placeOrder` directly with a crafted `order.inputs` array — there is no validation blocking duplicate `token` values in the Tron contract's inputs loop, unlike the mainline EVM contract at line 337. The only precondition is placing an order with a duplicate input token, an operation with no special permissions.

### Recommendation
Port the mainline guard into the Tron `placeOrder`: reject (or otherwise reject/merge with explicit accounting) any `order.inputs` entry whose token already has a non-zero `_orders[commitment][token]` value before crediting escrow, exactly as `evm/src/apps/IntentGatewayV2.sol:337` does. More generally, audit every place `_orders[commitment][token]` is read/written to confirm all of them agree on whether the map represents one leg or an aggregate, and add a regression test mirroring `testPlaceOrder_FeeOnTransferToken_WithProtocolFee` that places an order with two inputs sharing a token address and asserts it reverts (or is settled correctly) rather than silently merging escrow.

### Proof of Concept
1. Deploy the Tron `IntentGatewayV2` and call `placeOrder` with `order.inputs = [ {token: USDC, amount: 100}, {token: USDC, amount: 50} ]` and two output legs.
2. Observe `_orders[commitment][USDC]` becomes `150` (the sum) instead of reverting, because neither branch in `evm/tron/contracts/apps/IntentGatewayV2.sol:410-462` checks for a pre-existing non-zero value before the `+=`.
3. Trigger settlement/withdrawal logic that assumes a single-leg mapping for that token (as the mainline `_fillSameChain` full-balance-release path does) to observe one leg draining the combined `150` while the other leg's expected release finds the bucket already zeroed, reverting with `UnknownOrder` and permanently stranding that leg's intended recipient.

**Note:** I was not able to fully trace the Tron contract's own fill/settlement counterpart (its `fillOrder`/cross-chain confirmation path was not retrieved in full) within the available tool budget, so I cannot show the exact downstream call that drains the merged bucket in the Tron contract itself. The concrete, verified evidence is the divergence in `placeOrder` input validation between the two sibling implementations, and the mainline contract's own settlement code (`_fillSameChain`) demonstrates why merging escrow buckets is unsafe once a per-leg "release full remaining balance" pattern is present elsewhere in the codebase. Confirming full exploitability end-to-end on the Tron contract would require reading its complete `fillOrder`/`onAccept` cross-chain settlement logic, which should be verified in a follow-up session with full repository access.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-705)
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

            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
            unchecked {
                ++i;
            }
        }
```
