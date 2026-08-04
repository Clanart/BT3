Confirmed: `withdraw()` in `evm/tron/contracts/apps/IntentGatewayV2.sol:682-721` pays out exactly the ledger amount `_orders[body.commitment][token]` via a raw `token.call(transfer, beneficiary, amount)` without ever checking the gateway's real balance of that token, and `_orders[commitment][token]` was credited in `placeOrder` based on the requested/reduced amount rather than the amount actually received.

### Title
Escrow ledger credited with requested amount instead of actual received amount enables cross-order fund drain via fee-on-transfer/deflationary ERC20 inputs - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
`placeOrder` in the Tron `IntentGatewayV2` computes the escrow credit (`reducedInputs[i].amount`) from the user-supplied `order.inputs[i].amount` *before* any token transfer occurs, and then, in the direct-transfer path, calls `IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount)` without checking how much was actually received, crediting `_orders[commitment][token] += reducedInputs[i].amount` unconditionally [1](#0-0) . This is the same broken invariant as the OFT report: the amount "sent" (requested) is validated/used for accounting, but the amount actually "received" by the contract is never checked.

### Finding Description
The mainline EVM `IntentGatewayV2.sol` was already hardened for this exact class of bug: it snapshots balances before/after each ERC20 `transferFrom` and mutates `order.inputs[i].amount` to the *actual* delta received, so the commitment and escrow always reflect real custody [2](#0-1) .

The Tron variant, however, computes `reducedInputs` (the amount that will be credited to escrow) from the raw, pre-transfer `order.inputs[i].amount` up front [3](#0-2) , then in the no-predispatch branch does:
```solidity
IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
// Store reduced amount (after protocol fees) in escrow
_orders[commitment][token] += reducedInputs[i].amount;
```
with no balance check whatsoever [1](#0-0) . Even in the predispatch branch, the dust computation is based on `balance` held by the intermediate dispatcher before the final sweep into the gateway, not on what actually lands at the gateway after that sweep transfer, and the credited amount is still the pre-computed `reducedInputs[i].amount`, not a post-transfer delta at the gateway itself [4](#0-3) .

Consequently, if `order.inputs[i].token` is a fee-on-transfer/rebasing/deflationary ERC20 (a broad, unprivileged, permissionless choice made entirely by the order-placing user — no allow-list is enforced on token addresses), the contract's real token balance backing that specific commitment ends up smaller than the value recorded in `_orders[commitment][token]`.

Because `_orders` is a per-commitment ledger but the actual ERC20 balance is a single shared pool across all orders using that token, `withdraw()` pays out exactly the ledger amount via a raw low-level `transfer` call without any real-balance check [5](#0-4) . The shortfall created by one order's fee-on-transfer deposit is silently absorbed by other orders' legitimately-escrowed balances of the same token sitting in the same contract, so a later/earlier fill or refund for a *different* commitment can pay out more than the gateway ever received for that specific order — draining tokens rightfully belonging to other users.

### Impact Explanation
This breaks the "bridged assets/order escrow must move exactly once and only to the rightful beneficiary and amount" invariant. Any user can permissionlessly place an order using a fee-on-transfer/deflationary token as input, causing the escrow ledger to overstate real custody; subsequent settlement (`withdraw`, whether via `RedeemEscrow` or `RefundEscrow`) can pay out from balances funded by other users' unrelated orders, resulting in loss of funds / insolvency for the shared token pool. No malicious relayer, prover, or admin is required — this is triggerable purely by an unprivileged order placer choosing an arbitrary ERC20 as `order.inputs[i].token`.

### Likelihood Explanation
High: `order.inputs[i].token` is fully attacker-controlled and unvalidated; fee-on-transfer and deflationary ERC20s are common and require no special privileges to use. The mainline EVM contract's own test suite (`IntentGatewayV2SameChainTest.sol`) explicitly demonstrates and expects fee-on-transfer tokens to be handled by measuring actual received balance [6](#0-5) , confirming this is a realistic, anticipated token class for this contract family — yet the Tron deployment lacks the corresponding guard.

### Recommendation
In `evm/tron/contracts/apps/IntentGatewayV2.sol::placeOrder`, mirror the mainline fix: for every ERC20 input, snapshot `balanceOf(address(this))` before `safeTransferFrom` and compute the actually-received delta, use that delta (not the pre-transfer requested amount) to compute `reducedInputs`/protocol fees and to credit `_orders[commitment][token]`. Apply the same actual-balance-delta check to the final sweep step in the predispatch branch (dispatcher → gateway transfer), not just the dispatcher-held balance.

### Proof of Concept
1. Attacker deploys/uses an ERC20 `FOT` with a 5% transfer fee (self-controlled or an existing deflationary token).
2. Attacker places `OrderA` with `inputs = [{token: FOT, amount: 1000}]`. `reducedInputs[0].amount` is computed as 1000 (no protocol fee) before transfer [3](#0-2) . `safeTransferFrom` moves only 950 FOT into the gateway due to the fee, but `_orders[commitmentA][FOT] += 1000` is credited regardless [1](#0-0) .
3. A separate, legitimate user places `OrderB` with the same `FOT` token, correctly depositing (after fee) into the same contract balance pool.
4. When `OrderA` is filled/redeemed, `withdraw()` transfers 1000 FOT to the beneficiary directly off the shared contract balance, without checking that only 950 FOT was ever actually received for `OrderA` [5](#0-4) .
5. The extra 50 FOT paid out comes from `OrderB`'s deposited balance, which later reverts or under-pays when `OrderB` attempts its own redemption/refund — funds have been misappropriated across unrelated orders.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L353-379)
```text
        if (protocolFeeBps > 0) {
            reducedInputs = new TokenInfo[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                uint256 originalAmount = order.inputs[i].amount;
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
                address token = address(uint160(uint256(order.inputs[i].token)));

                // Emit DustCollected for protocol fee if non-zero
                if (protocolFee > 0) emit DustCollected(token, protocolFee);

                reducedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: reducedAmount});
                unchecked {
                    ++i;
                }
            }

            // Temporarily swap inputs to calculate commitment with reduced amounts
            TokenInfo[] memory originalInputs = order.inputs;
            order.inputs = reducedInputs;
            commitment = keccak256(abi.encode(order));
            order.inputs = originalInputs;
        } else {
            // No protocol fees, use order.inputs directly
            reducedInputs = order.inputs;
            commitment = keccak256(abi.encode(order));
        }
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

**File:** evm/src/apps/IntentGatewayV2.sol (L288-292)
```text
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
                }
```

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L2256-2308)
```text
    /// @notice Escrow correctly reflects actual received amount for fee-on-transfer tokens.
    function testPlaceOrder_FeeOnTransferToken_EscrowMatchesReceived() public {
        // Deploy a 1% fee-on-transfer token
        FeeOnTransferToken fot = new FeeOnTransferToken(100); // 1% = 100 bps
        fot.mint(user, 10000 * 1e18);

        uint256 inputAmount = 1000 * 1e18;
        uint256 expectedReceived = inputAmount - (inputAmount * 100) / 10000; // 990

        TokenInfo[] memory inputs = new TokenInfo[](1);
        inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(fot)))), amount: inputAmount});

        TokenInfo[] memory outputAssets = new TokenInfo[](1);
        outputAssets[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 900 * 1e18});

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

        vm.startPrank(user);
        fot.approve(address(intentGateway), inputAmount);
        intentGateway.placeOrder(order, bytes32(0));
        vm.stopPrank();

        // Gateway should hold only what it actually received
        assertEq(fot.balanceOf(address(intentGateway)), expectedReceived, "Gateway balance should match received amount");

        // Reconstruct the order as placeOrder would have mutated it
        order.user = bytes32(uint256(uint160(user)));
        order.source = host.host();
        order.nonce = 0;
        order.inputs[0].amount = expectedReceived;
        bytes32 commitment = keccak256(abi.encode(order));

        // Escrow should match actual received, not the user-specified amount
        assertEq(
            intentGateway._orders(commitment, address(fot)),
            expectedReceived,
            "Escrow should equal actual received amount"
        );
    }
```
