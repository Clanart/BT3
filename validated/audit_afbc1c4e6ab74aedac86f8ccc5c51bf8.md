## Title
Fee-on-transfer / deflationary token escrow over-crediting in `IntentGatewayV2.placeOrder` (Tron) — internal accounting diverges from actual token balance - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron variant of `IntentGatewayV2.placeOrder` credits the escrow map `_orders[commitment][token]` with the *nominal* order amount (minus the protocol fee), not the amount of tokens actually received via `safeTransferFrom`. For any non-standard ERC20 (fee-on-transfer, deflationary, rebasing-down) input token, the contract records more escrowed value than it actually holds, exactly the bug class described in the referenced Sherlock report. The parallel EVM implementation (`evm/src/apps/IntentGatewayV2.sol`) already fixed this by measuring the pre/post balance delta; the Tron contract was not patched the same way.

### Finding Description
In `placeOrder`, the "no predispatch" branch does: [1](#0-0) 

```solidity
} else {
    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
}
// Store reduced amount (after protocol fees) in escrow
_orders[commitment][token] += reducedInputs[i].amount;
```

`reducedInputs[i].amount` is derived purely from the user-declared `order.inputs[i].amount` minus the protocol fee bps — it is never checked against the gateway's actual balance change: [2](#0-1) 

If `token` charges a transfer fee (or burns/reflects on transfer), `safeTransferFrom` moves fewer tokens into the gateway than `order.inputs[i].amount`, yet the escrow ledger is still incremented by the nominal (fee-reduced-only-by-protocol-fee) amount. The predispatch branch has the same defect for the final leg back to the gateway — it reconciles dust against `requiredAmount` on the *dispatcher*, but still credits `_orders` with `reducedInputs[i].amount`, not the amount the gateway itself received: [3](#0-2) 

By contrast, the mainline EVM contract measures the actual balance delta and mutates `order.inputs[i].amount` to that delta before computing the commitment/escrow: [4](#0-3) 

and this behavior is explicitly covered by tests for fee-on-transfer tokens: [5](#0-4) 

No equivalent test or balance-delta guard exists in the Tron contract's `placeOrder`.

Downstream, `withdraw()` blindly trusts `_orders[commitment][token]` and attempts to move that recorded amount out via a raw `token.call(transfer, ...)`: [6](#0-5) 

Since the recorded escrow can exceed the tokens actually custodied, the ledger for that token becomes globally insolvent: the sum of `_orders[*][token]` across all outstanding orders can exceed `IERC20(token).balanceOf(gateway)`.

### Impact Explanation
This breaks the invariant that escrowed accounting must equal (or be backed by) actual custodied balance — the same defect Sherlock flagged. Concretely on cross-chain intents:

1. A user (attacker or unaware party) places an order using a fee-on-transfer / deflationary token as input, with `order.inputs[i].amount` set to a large nominal figure. The gateway actually receives less, but escrows the larger, fee-adjusted-only-by-protocol-fee amount.
2. A solver fills the order, delivering real output tokens directly to the user (beneficiary) on the destination chain per the fill flow before any input-side check occurs.
3. When the settlement/withdrawal message reaches the source chain and `withdraw()` executes, the transfer of the (inflated) escrowed amount to the solver either reverts (`TransferFailed`, because the gateway doesn't actually hold that many tokens) or, being first out of a shared pool, drains real balance meant for other users' escrowed orders on that same token.
4. Net effect: the solver, who already paid out real value to the user, either never receives full compensation (fund loss for the solver) or other legitimate orders sharing that token become unable to withdraw their rightful escrow (fund lock/loss for other users) — i.e., the shared escrow ledger for that token is insolvent.

This matches the bounty's in-scope categories: loss of funds via mismatched escrow accounting/logic attack in bridge custody and intent settlement.

### Likelihood Explanation
Any unprivileged user can trigger this by placing an order with a token that has any transfer-time deduction — this includes tokens the user deploys themselves, or any legitimately listed stablecoin/asset that later upgrades to add a fee (the same "upgradeable token" concern the original Sherlock sponsor raised). No relayer, prover, or governance compromise is required — only a standard `placeOrder` call from an EOA, so likelihood is high wherever this Tron contract is deployed with non-hardened tokens.

### Recommendation
Mirror the fix already present in `evm/src/apps/IntentGatewayV2.sol`: measure `balanceOf(address(this))` before and after each `safeTransferFrom` (and after sweeping from the dispatcher in the predispatch path), and use that actual delta — not the nominal/fee-adjusted `order.inputs[i].amount` — both for the commitment hash and for the `_orders[commitment][token]` credit. Apply the same balance-delta measurement consistently to the Tron contract's predispatch dust-sweep step so `_orders` is always backed 1:1 by the gateway's real token balance.

### Proof of Concept
1. Deploy a `FeeOnTransferToken` (e.g. 50% transfer fee) as in the existing Solidity test suite pattern (`evm/tests/foundry/IntentGatewayV2SameChainTest.sol`, `FeeOnTransferToken` contract).
2. Call `IntentGatewayV2(tron).placeOrder(order, graffiti)` with `order.inputs[0] = {token: FOT, amount: 1000e18}` and `protocolFeeBps = 0` so `reducedInputs[0].amount == order.inputs[0].amount == 1000e18`.
3. Observe `IERC20(FOT).balanceOf(gateway) == 500e18` (after the 50% fee) while `_orders[commitment][FOT] == 1000e18` — the escrow ledger is double what is actually held.
4. Have a solver fill the order and deliver real output tokens to the user.
5. On settlement, `withdraw()` attempts to transfer `1000e18` FOT to the solver; this reverts with `TransferFailed` (or succeeds partially/drains other orders' balance if the gateway holds pooled FOT from multiple orders) — demonstrating the solver either loses funds or other escrowed orders for the same token become unwithdrawable.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L356-364)
```text
                uint256 originalAmount = order.inputs[i].amount;
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
                address token = address(uint160(uint256(order.inputs[i].token)));

                // Emit DustCollected for protocol fee if non-zero
                if (protocolFee > 0) emit DustCollected(token, protocolFee);

                reducedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: reducedAmount});
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

**File:** evm/src/apps/IntentGatewayV2.sol (L282-298)
```text
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
                }

                unchecked {
                    ++i;
                }
            }
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
