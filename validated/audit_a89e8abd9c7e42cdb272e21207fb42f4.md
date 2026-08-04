## Title
Fee-on-transfer double-taxation makes the Tron `IntentGatewayV2.placeOrder` credit escrow for more tokens than the gateway actually custodies — (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The mainline EVM `IntentGatewayV2.placeOrder` (`evm/src/apps/IntentGatewayV2.sol:198-298`) is careful to measure the *actual* tokens received by the gateway (snapshotting balances before/after every transfer, including through the predispatch `CallDispatcher` hop) and mutates `order.inputs[i].amount` to the real received amount before computing the commitment and crediting escrow. The Tron fork of the same contract, `evm/tron/contracts/apps/IntentGatewayV2.sol`, drops this "actual received" accounting in the non-predispatch branch and part of the predispatch branch: it credits `_orders[commitment][token] += reducedInputs[i].amount` (the fee-reduced, user-declared amount) unconditionally, without ever re-checking the gateway's real token balance delta.

### Finding Description
In `placeOrder` (non-predispatch branch), tokens are pulled with `IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount)` and then the escrow ledger is incremented by `reducedInputs[i].amount` — a value derived purely from `order.inputs[i].amount` minus the protocol fee, computed *before* the transfer: [1](#0-0) 

For fee-on-transfer / deflationary ERC-20 tokens, `safeTransferFrom` delivers strictly less than `order.inputs[i].amount` to the gateway, yet `_orders[commitment][token]` is still credited with the full `reducedInputs[i].amount`. The predispatch branch has the same gap: it computes `dust = balance - requiredAmount` from the dispatcher-side balance only, and stores `_orders[commitment][token] += reducedInputs[i].amount` without verifying what the *gateway* itself actually received after the second transfer hop from the dispatcher — a hop that, for a fee-on-transfer token, taxes the transfer again: [2](#0-1) 

Compare this to the audited/hardened EVM path, which snapshots `balancesBefore` and computes `received = balance - balancesBefore` for every token leg — both in the direct-transfer branch and post-`CallDispatcher` sweep — and only credits/commits the amount actually observed on-chain: [3](#0-2) 

`_orders[commitment][token]` is a shared per-token accounting bucket (not a segregated vault keyed to real balances), so once it is over-credited relative to the gateway's true `IERC20(token).balanceOf(address(this))`, the ledger becomes globally insolvent for that token: `_orders` sums across all commitments can exceed the contract's actual balance.

### Impact Explanation
When the corresponding order is later settled — same-chain `withdraw()`/cross-chain `RedeemEscrow` handling releases `_orders[commitment][token]` to the solver, or `cancelOrder` refunds it to the user — the transfer can succeed by draining balance that rightfully belongs to *other* users' unrelated escrowed orders in the same token, since the shared ledger no longer matches custody. This is a genuine "loss of funds to the wrong beneficiary" / fund-shortfall condition: a legitimate order's escrow release can consume another order's real, honestly-deposited tokens, ultimately leaving some other user's `withdraw()`/`cancelOrder()` unable to be paid in full or reverting on `safeTransfer` due to insufficient contract balance. This directly matches the bounty's "stealing or loss of funds" / "transaction manipulation" impact classes, and requires no relayer, prover, or governance compromise — any user can place an order for a fee-on-transfer token as an unprivileged attacker primitive.

### Likelihood Explanation
This is triggerable by any user who places an order using a deflationary/fee-on-transfer ERC-20 as an input token (a well-known, common token category), with no cooperation from any privileged or off-chain actor needed. The mainline EVM contract explicitly defends against exactly this scenario (see its `balancesBefore`/`received` accounting and dedicated fee-on-transfer tests), confirming the maintainers recognize the risk class — the Tron variant appears to have regressed this protection, making the likelihood of accidental or deliberate triggering by real fee-on-transfer tokens on Tron/TRC20 high once such a token is used as an order input.

### Recommendation
Apply the same actual-received accounting used in `evm/src/apps/IntentGatewayV2.sol` to the Tron variant: snapshot the gateway's token balance immediately before each `safeTransferFrom`/predispatch sweep and credit `_orders[commitment][token]` with the measured delta (`balanceAfter - balanceBefore`) rather than the pre-computed `reducedInputs[i].amount`. Additionally mutate the escrowed/commitment-relevant `order.inputs` to the observed amount so on-chain custody and the accounting ledger stay reconciled for every token leg, in both the direct-transfer and predispatch-via-`CallDispatcher` code paths.

### Proof of Concept
1. Deploy the Tron `IntentGatewayV2` with a fee-on-transfer ERC-20 (e.g., 1% transfer tax) registered as a valid input token, with `protocolFeeBps = 0` for simplicity.
2. User A calls `placeOrder` with `order.inputs[0].amount = 1000` of the FOT token (non-predispatch branch). `safeTransferFrom` actually delivers only `990` to the gateway (`balanceOf(gateway)` increases by 990), but `_orders[commitmentA][token]` is credited `1000` (since `reducedInputs[i].amount == order.inputs[i].amount` when fee is 0).
3. User B separately places a normal order (any non-FOT token or a correctly-received FOT order) that legitimately escrows tokens of the same FOT token address, e.g. 500 tokens actually held by the gateway.
4. Gateway's real token balance is `990 + 500 = 1490`, but the sum of `_orders[...][token]` ledger entries is `1000 + 500 = 1500` — a 10-token deficit.
5. When order A is filled/cancelled and `_orders[commitmentA][token]` (1000) is paid out via `safeTransfer`, and subsequently order B attempts to redeem its legitimately-escrowed 500, the contract's real balance (1490 total, 490 remaining after A's full payout) is insufficient to fully honor B — B's `withdraw()`/`cancelOrder()` reverts or is left unpayable, demonstrating the loss/lock of another user's genuinely deposited funds caused solely by A's fee-on-transfer over-credit.

*Note: this analysis is scoped to the Tron contract fork; I was not able to fully verify whether other downstream consumers (e.g., `withdraw()` implementation further down the file, beyond line 560) apply any additional balance-reconciliation safeguard, since the file extends beyond what I could inspect in this session — a Devin session with full file access would be needed to confirm the exact `withdraw()` logic doesn't already independently cap payouts by real balance.*

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L381-443)
```text
        // escrow tokens
        uint256 msgValue = msg.value;
        if (order.predispatch.call.length > 0 && order.predispatch.assets.length > 0) {
            address dispatcher = _params.dispatcher;

            // Transfer all predispatch assets to the call dispatcher
            uint256 assetsLen = order.predispatch.assets.length;
            for (uint256 i; i < assetsLen;) {
                address token = address(uint160(uint256(order.predispatch.assets[i].token)));
                uint256 amount = order.predispatch.assets[i].amount;

                if (token == address(0)) {
                    if (amount > msgValue) revert InsufficientNativeToken();
                    msgValue -= amount;

                    (bool sent,) = dispatcher.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
                }

                unchecked {
                    ++i;
                }
            }

            // Execute the call dispatcher with predispatch call
            ICallDispatcher(dispatcher).dispatch(order.predispatch.call);

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

            // Execute transfer calls from call dispatcher
            ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls));
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

**File:** evm/src/apps/IntentGatewayV2.sol (L260-298)
```text
            // Measure actual received, emit dust for excess, update order.inputs.
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 received;
                if (token == address(0)) {
                    received = address(this).balance - balancesBefore[i];
                } else {
                    received = IERC20(token).balanceOf(address(this)) - balancesBefore[i];
                }

                if (received > order.inputs[i].amount) {
                    uint256 dust = received - order.inputs[i].amount;
                    emit DustCollected(token, dust);
                } else {
                    order.inputs[i].amount = received;
                }

                unchecked {
                    ++i;
                }
            }
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
