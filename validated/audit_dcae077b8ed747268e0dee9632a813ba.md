## Analysis

The external report's core invariant — *"credited/escrowed accounting must equal actually-received token balance, not the nominal transfer amount"* — has a real, exploitable local analog in the Tron deployment of the Intent Gateway.

### The fixed version vs. the vulnerable version

The canonical EVM `IntentGatewayV2.sol` was explicitly hardened against fee-on-transfer tokens: it snapshots the balance before and after `safeTransferFrom` and uses the **actual received delta** to set `order.inputs[i].amount` before computing the commitment and crediting escrow: [1](#0-0) 

This is confirmed by dedicated tests (`testPlaceOrder_FeeOnTransferToken_EscrowMatchesReceived`, `testPlaceAndFill_FeeOnTransferToken_RoundTrip`): [2](#0-1) 

The **Tron variant** (`evm/tron/contracts/apps/IntentGatewayV2.sol`), however, credits escrow directly from the user-declared, protocol-fee-reduced amount without ever checking the actual balance received from `safeTransferFrom`: [3](#0-2) 

The same unguarded pattern appears in the predispatch branch, where `_orders[commitment][token] += reducedInputs[i].amount` is credited from the nominal amount rather than the measured dust/received balance used for the sweep-call path: [4](#0-3) 

### Downstream impact: `withdraw()`

When an order is later redeemed (`RedeemEscrow`, on a successful fill) or refunded (`RefundEscrow`/`cancelOrder`), `withdraw()` pays out `body.tokens[i].amount` — the same nominal, fee-inflated amount — and only checks that the escrow slot is non-zero, never that the contract actually holds that balance: [5](#0-4) 

## Finding

### Title
Fee-on-transfer tokens cause escrow over-crediting and cross-order fund loss in the Tron IntentGatewayV2 - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
`placeOrder` in the Tron `IntentGatewayV2` credits `_orders[commitment][token]` using the user-specified (protocol-fee-adjusted) input amount rather than the token amount actually received by the contract, unlike the hardened `evm/src/apps/IntentGatewayV2.sol` which measures the pre/post balance delta.

### Finding Description
For any ERC-20 with a transfer fee, deflationary/rebasing mechanic, `safeTransferFrom(msg.sender, address(this), order.inputs[i].amount)` [6](#0-5)  delivers less than `order.inputs[i].amount` to the gateway, yet the escrow map is still credited with `reducedInputs[i].amount` computed from the full nominal amount [7](#0-6) . Across multiple concurrent orders using the same fee-on-transfer token, the sum of `_orders[commitment][token]` values recorded on-chain exceeds the contract's real token balance. `withdraw()` never reconciles the recorded escrow against the actual `IERC20.balanceOf(address(this))`; it only requires the commitment's slot to be non-zero [8](#0-7)  before issuing a `transfer` for the full recorded amount.

### Impact Explanation
This breaks the "move exactly once and only the rightful amount" invariant for bridged assets in escrow. Once several orders using a fee-on-transfer token are placed, the contract is structurally insolvent for that token: whichever beneficiary redeems or is refunded last will have their `transfer` fail (fund lock) or, if some prior redemptions already drained the real balance below what remains owed, later legitimate solver/user redemptions become permanently unrecoverable — a direct loss of funds for other order participants, not the attacker. This is a production custody defect in bridge escrow.

### Likelihood Explanation
Any unprivileged user can trigger this simply by placing an order with a fee-on-transfer/deflationary token as input — no relayer, prover, or admin compromise is needed. Solidity does not restrict `order.inputs[i].token` to a whitelist in this contract, so exploitation only requires the existence of, or governance-approval of, any fee-charging ERC-20 on the Tron deployment (e.g., a listed USDT-like token with a fee switch, which is exactly the class of token cited in the source report).

### Recommendation
Mirror the fix already present in `evm/src/apps/IntentGatewayV2.sol`: snapshot `IERC20(token).balanceOf(address(this))` before and after `safeTransferFrom`, and use the measured delta (not the nominal `order.inputs[i].amount`) both for the commitment hash and for the value credited to `_orders[commitment][token]`. Apply the same balance-delta measurement to the predispatch/sweep branch.

### Proof of Concept
1. Deploy a 5%-fee ERC-20 (`FeeOnTransferToken`, as already used in the Foundry test suite) and register it as an input token on the Tron `IntentGatewayV2`.
2. User A calls `placeOrder` with `inputs[0].amount = 1000`. Gateway actually receives 950, but `_orders[commitmentA][token] = 1000` (or `1000 - protocolFee`).
3. User B does the same, gateway now holds `950 + 950 = 1900` tokens, but `_orders` mappings collectively claim `1000 + 1000 = 2000` (minus fees) is escrowed.
4. Both orders get filled/redeemed via `RedeemEscrow`; `withdraw()` attempts to `transfer` the full recorded amounts. The second redemption's `transfer` reverts or under-delivers because the gateway's real balance (1900) is less than the sum of recorded escrow entitlements (2000), permanently locking or losing the shortfall for the last claimant.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L288-292)
```text
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
                }
```

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L2287-2307)
```text
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L444-463)
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
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-700)
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

```
