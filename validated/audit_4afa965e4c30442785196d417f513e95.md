## Title
Fee-on-transfer/short-transfer token escrow accounting uses pre-sweep balance instead of gateway-verified received amount, enabling shared-pool fund drain — (`evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.placeOrder()` on Tron (predispatch/swap path) credits the order's escrow ledger `_orders[commitment][token]` using amounts derived from the pre-swap declared `order.inputs[i].amount` and the dispatcher's pre-transfer balance, but never verifies how many tokens the gateway itself actually receives after the final sweep transfer. This is the same root defect as JalaSwap M-2: a downstream accounting value is computed from an amount taken *before* a value-changing operation (here, the ERC20 transfer from the `CallDispatcher` back to the gateway) instead of the amount actually received *after* it.

### Finding Description
In `placeOrder()`, `reducedInputs` (escrow credit per token, after protocol fee) and the order `commitment` are computed at lines 342–379, using the *raw declared* `order.inputs[i].amount`, before the predispatch call or the sweep transfer ever executes.

Later, in the predispatch branch (lines 407–443):
```solidity
balance = IERC20(token).balanceOf(dispatcher);
if (balance < requiredAmount) revert InvalidInput();
transferCalls[i] = Call({to: token, value: 0, data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)});
...
_orders[commitment][token] += reducedInputs[i].amount;   // <-- credited from PRE-transfer value, not verified receipt
```
`balance` here is only the dispatcher's balance *before* the final transfer to `address(this)`. The actual amount the gateway itself receives is never measured — there is no `balanceOf(address(this))` before/after snapshot around `ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls))` (line 443). For any ERC20 with a transfer fee, rebasing behavior, or a blacklist/partial-transfer quirk, the tokens landing in the gateway can be less than `balance`, and therefore less than the `reducedInputs[i].amount` credited to escrow.

This directly mirrors the JalaSwap bug: the router computed `swapExactTokensForETH(amountIn, ...)` using the pre-wrap `amountIn` instead of the post-wrap amount actually available, silently under/over-crediting a shared-balance accounting model.

Contrast with the sibling EVM contract `evm/src/apps/IntentGatewayV2.sol`, which correctly guards against this: it snapshots `balancesBefore[i] = IERC20(token).balanceOf(address(this))` before the sweep dispatch, and after the sweep computes `received = IERC20(token).balanceOf(address(this)) - balancesBefore[i]`, then sets `order.inputs[i].amount = received` when the actual amount is less than requested, *before* protocol fees/commitment/escrow are computed. The Tron variant lacks this reconciliation entirely.

Because `_orders[commitment][token]` is a bookkeeping value drawn from a **shared token balance pool** (all orders' input tokens of the same ERC20 sit in the same contract balance, per `withdraw()` at lines 682–705, which just does `token.call(transfer(beneficiary, amount))` against the pool), any escrow entry that is over-credited relative to actually-held tokens is paid out of the balance funded by *other* orders/users.

### Impact Explanation
This causes false-state acceptance in the intent-escrow ledger and unauthorized transfer of value: an order using a fee-on-transfer/deflationary ERC20 as input (with a non-trivial `predispatch.call`) can be escrowed for more than the gateway actually received. When that order later reaches `withdraw()` (via `RedeemEscrow` on a successful cross-chain fill, `RefundEscrow` on cancellation, or the same-chain cancel path), the beneficiary receives the fully-credited (inflated) amount from the shared per-token balance, which is only solvent because it dips into tokens legitimately escrowed for other users' orders. This is loss/theft of user funds and a double-accounting/false-settlement bug, matching the bounty's "stealing or loss of funds" and "false proof/state acceptance" categories.

### Likelihood Explanation
Exploitability requires no privileged role — any user can call the public `placeOrder()` with a predispatch call and a fee-on-transfer or otherwise lossy ERC20 as an input token; this is well within an ordinary unprivileged user's control (they choose both the token and the predispatch calldata). The only friction is that the destination token must exhibit a receive-side loss on transfer (fee-on-transfer, rebasing, or blacklist-partial behavior) — such tokens are common enough in the wild (e.g. STA-style, some rebasing/tax tokens) that this is a realistic production risk on Tron given the codebase's own EVM sibling already had to add the missing check.

### Recommendation
In `evm/tron/contracts/apps/IntentGatewayV2.sol::placeOrder()`, mirror the fix already present in `evm/src/apps/IntentGatewayV2.sol`:
1. Snapshot `balanceOf(address(this))` before dispatching the sweep `transferCalls`.
2. After the sweep, compute `received = balanceOf(address(this)) - balanceBefore` per token.
3. Reconcile `order.inputs[i].amount` (and hence `reducedInputs`/commitment/escrow credit) to the actually-received amount rather than the pre-transfer `balance`/declared amount, before crediting `_orders[commitment][token]`.
4. Move the `reducedInputs`/commitment computation to occur *after* this reconciliation, as done in the main EVM contract.

### Proof of Concept
1. Deploy/token: use an ERC20 with e.g. a 2% transfer fee (or any token where `transfer()` delivers less than the requested amount to the recipient) as `order.inputs[0].token`.
2. Attacker (as `msg.sender`) calls `placeOrder(order, graffiti)` with:
   - `order.predispatch.call` set to a harmless dispatcher call (e.g., a no-op `Call` or an approve) and `order.predispatch.assets` non-empty, so the predispatch branch (lines 383–443) executes.
   - `order.inputs[0].amount` = `X` (target amount), funded by the attacker with exactly `X` tokens transferred to the dispatcher.
3. During execution:
   - `balance = IERC20(token).balanceOf(dispatcher)` = `X` (post predispatch call, before the fee-loss transfer).
   - `requiredAmount = X`; check passes (`balance >= requiredAmount`).
   - Sweep transfer moves `balance` (`X`) from dispatcher to `address(this)`, but due to the 2% fee, the gateway's balance only increases by `0.98 * X`.
   - `_orders[commitment][token] += reducedInputs[0].amount` credits based on `X` (minus protocol fee), not `0.98 * X` — an over-credit of ~2% of `X` that is not backed by any tokens the attacker actually deposited into the shared pool.
4. Attacker cancels the order (same-chain cancel path or cross-chain `RefundEscrow`), triggering `withdraw()`, which pays out the full over-credited `_orders[commitment][token]` amount from the gateway's pooled token balance — funds that were deposited by other users' unrelated orders in the same token.
5. Repeating this with larger `X` or across multiple orders siphons off increasing amounts from the shared pool, eventually causing other legitimate users' withdrawals to revert (`InsufficientBalance`/`TransferFailed`) due to pool depletion, or letting the attacker directly extract more value than deposited. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L342-379)
```text
        // Calculate reduced inputs (after protocol fees) for commitment and escrow
        uint256 inputsLen = order.inputs.length;
        // Use destination-specific protocol fee, fallback to source chain fee if zero
        bytes32 destinationHash = keccak256(order.destination);
        uint256 protocolFeeBps = _destinationProtocolFees[destinationHash];
        if (protocolFeeBps == 0) {
            protocolFeeBps = _params.protocolFeeBps;
        }
        TokenInfo[] memory reducedInputs;
        bytes32 commitment;

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L407-443)
```text
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

**File:** evm/src/apps/IntentGatewayV2.sol (L229-298)
```text
            // Build sweep calls and snapshot gateway balances before the sweep.
            Call[] memory transferCalls = new Call[](inputsLen);
            uint256[] memory balancesBefore = new uint256[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;

                if (token == address(0)) {
                    uint256 balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                    balancesBefore[i] = address(this).balance;
                } else {
                    uint256 balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                    balancesBefore[i] = IERC20(token).balanceOf(address(this));
                }

                unchecked {
                    ++i;
                }
            }

            ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls));

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
