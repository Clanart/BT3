## Finding

The Tron deployment of the Intent Gateway (`evm/tron/contracts/apps/IntentGatewayV2.sol`) escrows fee-on-transfer/tax tokens by nominal amount instead of actual received amount, unlike the patched EVM version, creating an accounting mismatch that can drain other users' escrowed balances of the same token.

### Title
Fee-on-transfer token escrow over-accounting in Tron IntentGatewayV2 enables draining of other users' escrowed funds - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
The canonical EVM `IntentGatewayV2.placeOrder` was hardened against fee-on-transfer/rebasing ERC-20s: it measures the gateway's actual token balance delta after `safeTransferFrom` and mutates `order.inputs[i].amount` to the amount actually received before computing the commitment hash and crediting escrow [1](#0-0) . The Tron variant of the same contract never adopted this fix: it transfers `order.inputs[i].amount` via `safeTransferFrom` and then credits escrow with `reducedInputs[i].amount`, which is derived purely from the caller-supplied nominal `order.inputs[i].amount` minus the protocol fee — with no balance check at all [2](#0-1) [3](#0-2) .

### Finding Description
For any TRC20 input token that charges a transfer fee or rebases (common on Tron, e.g. tax tokens), `placeOrder` records `_orders[commitment][token] += reducedInputs[i].amount` using the nominal requested amount rather than what the gateway actually received in its balance [4](#0-3) . Because `_orders[commitment][token]` uses `+=` against a single per-token storage slot shared across all orders using that token, the ledger becomes an aggregate that is not 1:1 backed by the gateway's real on-chain balance. When `withdraw()` is later invoked — either through `onAccept` handling a cross-chain `RedeemEscrow`/`RefundEscrow` request, or through same-chain cancellation — it transfers `body.tokens[i].amount` directly via `token.call(...transfer...)` without verifying the gateway's actual balance against the aggregate ledger [5](#0-4) . Any inflated escrow credit for one order is fungible with other orders' real token balances in the same pooled contract balance, so the shortfall from one fee-on-transfer order is silently paid out of the tokens genuinely deposited by other, unrelated users of the same token.

### Impact Explanation
This directly matches the bounty's "stealing or loss of funds" and "transaction manipulation" categories for bridge custody: an unprivileged user can place an order using a fee-on-transfer token, causing the contract to record more escrowed balance than it actually holds for that token. When that inflated amount is later withdrawn (to a solver on `RedeemEscrow` or back to the user on `RefundEscrow`/cancellation), the excess is paid out of the shared token balance pool, which is backed by other legitimate users' unrelated escrowed deposits of the same token — a direct loss of funds for those other order holders, or a self-service overpayment/inflated refund for the attacker themselves in cases where per-order-specific shortfalls exceed contributions.

### Likelihood Explanation
Requires only a normal `placeOrder` call using a fee-on-transfer TRC20 as an order input — no privileged role, relayer, prover, or governance action is needed, matching the "public entrypoint, unprivileged attacker" requirement. The EVM sibling contract in this same repo was explicitly patched for this exact scenario (confirmed by dedicated tests `testPlaceOrder_FeeOnTransferToken_EscrowMatchesReceived`, `testPlaceOrder_FeeOnTransferToken_WithProtocolFee`, `testPlaceAndFill_FeeOnTransferToken_RoundTrip` [6](#0-5) ), confirming the maintainers consider this a real, exploitable class of bug in this exact contract family; the Tron variant simply lacks the corresponding fix.

### Recommendation
Apply the same balance-delta measurement used in `evm/src/apps/IntentGatewayV2.sol` to the Tron contract: snapshot `IERC20(token).balanceOf(address(this))` before and after each `safeTransferFrom`, use the actual delta as `order.inputs[i].amount` before computing the protocol fee, commitment hash, and escrow credit, exactly as done at [1](#0-0) .

### Proof of Concept
1. Deploy the Tron `IntentGatewayV2` with a fee-on-transfer TRC20 token `T` (1% transfer tax) registered as a valid input asset.
2. User A places an order with `inputs = [{token: T, amount: 1000}]`. `safeTransferFrom` moves 1000 nominal units, but the gateway's actual `T` balance only increases by 990 (1% burned/taxed on transfer). The contract nonetheless credits `_orders[commitment_A][T] += reducedInputs[0].amount` (≈1000 minus protocol fee), i.e., roughly 10 units more than it actually received [7](#0-6) .
3. User B independently places an unrelated order with a clean (non-fee) transfer of `T`, correctly funding the gateway's `T` balance by the full nominal amount, adding to the same aggregate pool via the same `+=` accounting.
4. When User A's order is filled/cancelled and `withdraw()` runs, the gateway transfers the inflated `_orders[commitment_A][T]` amount out of its pooled `T` balance [8](#0-7) , consuming part of the balance that was actually contributed by User B's order, since there is no per-order balance backing check.
5. User B's subsequent withdrawal then reverts or is short-paid because the token balance actually available is less than the sum of all ledger entries — funds are effectively misdirected/lost.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L288-292)
```text
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
                }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L352-379)
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
