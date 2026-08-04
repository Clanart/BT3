Confirmed: in `evm/tron/contracts/apps/IntentGatewayV2.sol`, the non-predispatch escrow path calls `IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount)` and then immediately credits `_orders[commitment][token] += reducedInputs[i].amount` using the **declared** input amount rather than measuring the actual balance delta, unlike the hardened `evm/src/apps/IntentGatewayV2.sol` which snapshots `balBefore`/`balAfter` and mutates `order.inputs[i].amount` to the actually-received amount before computing `reducedInputs` and crediting escrow. [1](#0-0) [2](#0-1) 

### Title
Escrow accounting in Tron `IntentGatewayV2.placeOrder` trusts declared input amount instead of actual tokens received, causing cross-order insolvency for non-standard ERC20s - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
The Tron variant of `IntentGatewayV2.placeOrder` credits the per-commitment escrow ledger `_orders[commitment][token]` using the user-declared `order.inputs[i].amount` (reduced only by the protocol fee), rather than the amount actually received by the contract via `safeTransferFrom`. The canonical EVM implementation in `evm/src/apps/IntentGatewayV2.sol` fixes exactly this class of bug by snapshotting the contract's token balance before and after the transfer and using the delta (`IERC20(token).balanceOf(address(this)) - balBefore`) to compute `order.inputs[i].amount`, which then flows into the fee calculation and escrow credit. The Tron contract lacks this balance-delta measurement in its non-predispatch branch.

### Finding Description
In the non-predispatch branch of `placeOrder` [1](#0-0) , the contract does:
```
IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
...
_orders[commitment][token] += reducedInputs[i].amount;
```
`reducedInputs[i].amount` is derived purely from the declared `order.inputs[i].amount` minus the protocol fee bps [3](#0-2) . There is no check that the contract's actual token balance increased by that amount. For any token whose transferred amount can diverge from the requested amount — fee-on-transfer tokens, deflationary/burn-on-transfer tokens, or (per the external report's underlying invariant) tokens whose accounted balance does not track actual custody 1:1 — the escrow ledger will record more value than the contract actually holds for that commitment.

Since `_orders` is a per-commitment-per-token mapping backed by one shared pool of the contract's actual token balance, over-crediting one commitment does not fail atomically; it silently creates a ledger entry unbacked by real tokens. When that (or any other) order is later settled via `withdraw`/`_withdraw`, the contract attempts `IERC20(token).transfer(beneficiary, amount)` for the recorded (inflated) amount [4](#0-3) . Because the aggregate real balance is short by the shortfall from the mis-accounted order, whichever legitimate order is settled later ends up either reverting (denial of funds/lock for a different user) or, in more complex sequences, an attacker can weaponize this: place an order with a token/proxy configuration that under-delivers actual balance while the recorded escrow amount is inflated, get it filled and withdraw the full recorded (inflated) amount, effectively draining tokens that belong to other users' concurrently-escrowed orders sharing the same token.

This mirrors the report's root cause exactly: the code assumes 1:1 correspondence between "amount recorded/requested" and "actual custodied balance," and fails when the underlying asset's transfer/balance semantics don't guarantee that — the same broken invariant as unaccounted stETH rebasing, just here manifesting through fee-on-transfer/burn-on-transfer token mechanics instead of rebasing.

### Impact Explanation
This breaks the core custody invariant that escrowed accounting equals actual custodied tokens per the Hyperbridge pivots ("Bridged assets, order escrow, refunds... must move exactly once and only to the rightful beneficiary and amount"). An unprivileged user placing an order with a non-standard token can cause the contract to record and later pay out more tokens than it received, at the expense of other users' escrowed balances of the same token — a direct fund-loss/wrong-amount scenario reachable via the public `placeOrder` entrypoint, with no relayer, prover, or admin involvement required.

### Likelihood Explanation
Any ERC20 with fee-on-transfer, burn-on-transfer, or similar non-standard transfer semantics accepted as an input token triggers this deterministically — no race condition or privileged actor needed. The project's own test suite (`evm/tests/foundry/IntentGatewayV2SameChainTest.sol`) explicitly demonstrates and tests for this exact class of bug against the canonical `evm/src/apps/IntentGatewayV2.sol`, confirming fee-on-transfer tokens are an anticipated, in-scope input type [5](#0-4) , yet the Tron deployment of the same contract was not given the equivalent balance-delta fix.

### Recommendation
In `evm/tron/contracts/apps/IntentGatewayV2.sol`'s `placeOrder`, mirror the fix already present in `evm/src/apps/IntentGatewayV2.sol`: snapshot `IERC20(token).balanceOf(address(this))` before `safeTransferFrom`, compute the actual received amount as the post-transfer balance delta, mutate `order.inputs[i].amount` to that value, and only then compute `reducedInputs`/the commitment/the escrow credit from the actually-received amount — for both the predispatch and non-predispatch branches.

### Proof of Concept
1. Deploy a fee-on-transfer ERC20 (1% burn on transfer) and have a user approve/hold balance, following the same pattern as `FeeOnTransferToken` in `evm/tests/foundry/IntentGatewayV2SameChainTest.sol` (lines 2501-2547).
2. User calls Tron `IntentGatewayV2.placeOrder` with `order.inputs[0] = {token: FOT, amount: 1000e18}`. The contract's `safeTransferFrom` only delivers `990e18` to the contract, but `_orders[commitment][FOT]` is credited with `reducedInputs[0].amount` computed from the full `1000e18` (minus protocol fee only) — i.e., overstated by the transfer-fee amount.
3. A separate, legitimate order using the same FOT token is placed and correctly escrowed for its own actual amount.
4. When the first (mis-accounted) order is filled and its solver calls `withdraw`, the contract pays out the inflated recorded amount, which can only be satisfied by drawing down the FOT balance that actually belongs to the second legitimate order's escrow, causing the second order's later withdrawal to revert (fund lock) or, depending on withdrawal order, silently pay from the wrong pool of funds. [4](#0-3)

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

**File:** evm/src/apps/IntentGatewayV2.sol (L281-298)
```text
        } else {
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
