### Title
IntentGatewayV2 escrow accounting assumes a static, per-order 1:1 relationship between pooled ERC20 token balance and `_orders[commitment][token]` bookkeeping, which desyncs for rebasing/elastic-supply tokens and causes fund loss or lock for legitimate escrow holders - (File: `evm/src/apps/intentsv2/IntentsBase.sol`, `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Nibiru report's core broken invariant is: escrow accounting is fixed at deposit time, but the actual token balance held in custody can drift afterward (rebasing), so later withdrawals release a stale, no-longer-backed amount. Hyperbridge's `IntentGatewayV2` reproduces the identical pattern in its intent-escrow flow: `placeOrder` carefully measures the *actual* balance received (to support fee-on-transfer tokens), but the withdrawal path (`_withdraw` / `withdraw`) releases the *fixed, previously recorded* `amount` from `_orders[commitment][token]` with a plain `safeTransfer`/`transfer` call and no balance reconciliation, while all orders for the same token share one pooled contract balance.

### Finding Description
At placement, `IntentGatewayV2.placeOrder` deliberately snapshots balances before/after transfers to cope with tokens whose transferred amount differs from the requested amount (fee-on-transfer tokens): [1](#0-0) 

This actual-received amount is what gets written into escrow bookkeeping (`order.inputs[i].amount`, then `_orders[commitment][token] += reducedInputs[i].amount` in the Tron variant): [2](#0-1) 

However, the withdrawal side of the same escrow — used both for solver settlement (`RedeemEscrow`) and for refunds/cancellations (`RefundEscrow`) — does the opposite: it trusts the stored ledger amount and moves it with a fixed-amount transfer, never re-checking the real token balance against the sum of outstanding `_orders` entries: [3](#0-2) [4](#0-3) 

Critically, `_orders[commitment][token]` is a *logical* ledger entry, not a segregated/isolated escrow — all orders for the same ERC20 token share one physical contract balance. The guard in both `withdraw`/`_withdraw` only checks that the ledger entry is non-zero (`if (escrowed == 0) revert UnknownOrder()` / `if (_orders[body.commitment][token] == 0) revert UnknownOrder()`), never that the token's *actual* `balanceOf(this)` still covers the amount being released. If the escrowed token's balance can change independent of transfers (rebasing, elastic supply, negative-yield/slashing tokens), the sum of all outstanding `_orders[...]` entries for that token can exceed the real balance held by the gateway by the time settlement/cancellation messages arrive (which, for cross-chain orders, is asynchronous and can be delayed by finality/challenge periods).

This is the exact analog of the Nibiru bug: the protocol assumes escrowed-token-balance-in ≡ escrowed-token-balance-out at redemption time, an invariant that placeOrder's own fee-on-transfer handling proves the authors know can break, yet the withdrawal path never re-verifies it.

### Impact Explanation
- If cumulative rebases are negative (or the token can be slashed/rebase-down while held), the pooled physical balance can become insufficient to cover the sum of ledger entries. The transfer that would drain the last available balance succeeds (draining funds meant for other still-outstanding orders on the same token), while later, otherwise-legitimate withdrawals for other orders permanently revert — a "wrong beneficiary/wrong amount" outcome in aggregate: some order-holders get paid out of funds that logically belonged to others, and the remainder are permanently locked out of their rightful escrow.
- If cumulative rebases are positive, the surplus sitting in the contract for that token is never reconciled or attributed to any order or protocol-dust sweep path (dust sweeps only trigger from measured deltas during placement/predispatch/postdispatch, not for post-escrow appreciation), so value is permanently stranded, unclaimed by anyone.
- This directly matches the Hyperbridge impact gate: "Bridged assets, order escrow, refunds... must move exactly once and only to the rightful beneficiary and amount" — the shared-pool + fixed-ledger design breaks this for any token whose balance moves independent of transfers while escrowed.

### Likelihood Explanation
This does not require a malicious peer, relayer, or governance actor — it is triggered purely by the mechanics of the underlying ERC20 token combined with the normal, unprivileged `placeOrder`/`fillOrder`/`cancelOrder` flow available to any user. It requires only that an intent order be placed using an elastic-supply/rebasing token as input — the same class of token the contract's own `placeOrder` fee-on-transfer accommodation implies is a supported/considered case, but which the withdrawal path never re-validates. The contract does not appear to maintain an explicit allow-list restricting input tokens to non-rebasing assets (the fee-on-transfer measurement logic is unconditional for all ERC20 inputs), so any token added to an order is subject to this gap.

### Recommendation
- Either explicitly reject/allow-list only tokens verified to have balances that change solely through transfers (document this as an integration constraint, similar to `StreamingYieldVault`'s explicit "standard ERC-20 only" warning), or
- Re-validate actual token balance at withdrawal time: measure `balanceOf(this)` before/after the transfer (as already done at placement) and cap/reconcile the amount released against real availability, reverting or pro-rating rather than allowing first-claimant drainage of other orders' escrow, and
- Track per-order escrow using balance deltas/shares of the pooled balance rather than fixed absolute amounts, so any rebase-driven surplus/deficit is distributed proportionally instead of causing first-come-first-served fund loss.

### Proof of Concept
1. User A places an intent order (`placeOrder`) with `RBT` (a rebasing ERC-20) as the input token, amount `1000`. `IntentGatewayV2.placeOrder` measures actual received `1000` and sets `_orders[commitmentA][RBT] = 1000`.
2. User B places a second order with the same `RBT` token, amount `1000`. `_orders[commitmentB][RBT] = 1000`. Contract's real `RBT.balanceOf(gateway) == 2000`.
3. `RBT` undergoes a negative rebase of 30% while both orders sit in escrow awaiting solver fills/cross-chain settlement (rebases occur globally, independent of any transfer to/from the gateway). Real balance is now `1400`, but ledger still records `1000 + 1000 = 2000` owed.
4. A solver fills order A on the destination chain; the `RedeemEscrow` message arrives and `withdraw`/`_withdraw` is invoked for commitment A with `amount = 1000`. `_orders[commitmentA][RBT]` is non-zero, so the check passes; `safeTransfer(beneficiaryA, 1000)` succeeds (contract still holds `1400`), leaving real balance `400`.
5. Order B is filled or cancelled; `withdraw` is invoked with `amount = 1000` for commitment B. `_orders[commitmentB][RBT]` is non-zero (passes the guard), but the actual `RBT` balance (`400`) is insufficient — `safeTransfer`/low-level `transfer` reverts (or, for tokens that don't revert on insufficient balance, transfers less than the recorded/expected amount), permanently denying User B their rightful escrowed funds even though the ledger says they are owed `1000`.

This demonstrates fund loss/lock for a legitimate order holder purely from unprivileged, normal use of `placeOrder`/settlement flows, with no reliance on a malicious relayer, prover, or admin.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L288-292)
```text
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
                }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L452-462)
```text
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
