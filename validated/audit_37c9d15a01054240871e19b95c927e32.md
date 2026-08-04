## Finding: Escrow crediting in Tron `IntentGatewayV2.placeOrder` trusts the nominal order amount instead of the actual ERC20 balance received

### Title
Escrow accounting uses caller-specified `order.inputs[i].amount` instead of measured `balanceOf` delta, allowing fee-on-transfer/deflationary tokens to overstate escrow and drain the pooled token balance - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
This is a direct structural analog of the reported bug class: a value that is *assumed* to represent an asset amount (`ethToStake()` / here, `order.inputs[i].amount`) is used in place of the *actual* measured balance (`address.balance()` / here, `IERC20(token).balanceOf(address(this))` delta). In the main, patched `evm/src/apps/IntentGatewayV2.sol`, `placeOrder` explicitly measures `balanceOf` before and after `safeTransferFrom` and mutates `order.inputs[i].amount` to the real received amount before crediting escrow (`evm/src/apps/IntentGatewayV2.sol:289-291`). The Tron variant of the same contract dropped this measurement in the non-predispatch path.

### Finding Description
In `evm/tron/contracts/apps/IntentGatewayV2.sol`, the `placeOrder` else-branch (no predispatch call) does: [1](#0-0) 

It calls `IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount)` and then unconditionally credits `_orders[commitment][token] += reducedInputs[i].amount`, where `reducedInputs[i].amount` is derived purely from `order.inputs[i].amount` (the caller-specified amount), never from the gateway's actual post-transfer token balance. Compare this to the corresponding, fixed logic in the main EVM contract, which measures `balBefore`/`balAfter` and rewrites `order.inputs[i].amount` to the real amount received: [2](#0-1) 

Because the Tron contract skips this measurement, if the input token charges a transfer fee (or is otherwise deflationary/rebasing), the gateway actually receives less than `order.inputs[i].amount`, yet records the full nominal amount in `_orders[commitment][token]`.

This corrupted escrow ledger entry is later paid out verbatim. `cancelOrder` (same-chain path) builds the withdrawal directly from `order.inputs` (the nominal, unmeasured amount) rather than the tracked escrow value, and `withdraw()` only checks existence (`!= 0`), not sufficiency, before transferring: [3](#0-2) [4](#0-3) 

`withdraw()` transfers `amount = body.tokens[i].amount` (the nominal, over-stated figure) out of the contract's pooled token balance, and only afterward attempts `_orders[body.commitment][token] -= amount`. Since the gateway's real token balance for that specific order is less than the recorded/nominal amount, the payout is satisfied out of the shared pool of tokens escrowed by *other* users' orders of the same token — this is a direct fund-loss/theft vector, not merely a bookkeeping cosmetic issue.

### Impact Explanation
An unprivileged attacker can:
1. Deploy or use any ERC20 with a transfer fee/deflationary mechanic (the contract places no restriction on token type).
2. Call `placeOrder` specifying `order.inputs[i].amount = X`; the gateway actually receives `X - fee` but escrows the full `X` in `_orders[commitment][token]`.
3. Call `cancelOrder` (same-chain path) or trigger the cross-chain `RefundEscrow`/`RedeemEscrow` path; `withdraw()` sends the full nominal `X` back to the attacker even though the gateway only ever held `X - fee` for that order.
4. The excess `fee` amount paid to the attacker is drawn from the gateway's aggregate token balance, i.e., other legitimate users' escrowed funds of the same token — a genuine loss/theft of bridged/escrowed assets, satisfying the "stealing or loss of funds" / "wrong beneficiary or amount" impact categories.

This requires no relayer, prover, or admin cooperation — it is exploitable purely by an unprivileged caller choosing which ERC20 to deposit.

### Likelihood Explanation
High for any deployment of this Tron variant that allows arbitrary ERC20 tokens as intent inputs (there is no allowlist check visible in the reviewed code). Fee-on-transfer and deflationary tokens are common and trivially deployable by an attacker who controls the input token side of their own order. The main EVM contract already had to be patched for exactly this class of issue (the `balBefore`/`balAfter` diffing present there), confirming the underlying flaw is realistic and previously identified by the team for the sibling contract — but the fix was not carried over to the Tron contract.

### Recommendation
Mirror the main EVM implementation's fix in the Tron contract: measure `IERC20(token).balanceOf(address(this))` before and after each `safeTransferFrom` in `placeOrder`, and credit `_orders[commitment][token]` using the actual received delta rather than the caller-specified `order.inputs[i].amount`. Additionally, harden `withdraw()` to pay out based on the tracked `_orders[commitment][token]` value (as the main contract's `_cancelSameChain` correctly does) rather than trusting the `amount` field embedded in the caller-supplied `WithdrawalRequest.tokens` array, and to use `min(requested, escrowed)` or an explicit sufficiency check instead of only an existence check.

### Proof of Concept
1. Deploy a fee-on-transfer ERC20 (e.g., 5% fee burned/retained on `transferFrom`), following the pattern already used in the repo's own test double `FeeOnTransferToken` (`evm/tests/foundry/IntentGatewayV2SameChainTest.sol:2502-2547`).
2. As `attacker`, call `IntentGatewayV2.placeOrder` on the Tron contract with `order.inputs[0] = {token: fotToken, amount: 1000e18}`. The gateway's real balance increases by `950e18`, but `_orders[commitment][fotToken]` is set to `950e18` (assuming no protocol fee) *or*, if `protocolFeeBps == 0`, is set to the full `1000e18` since `reducedInputs = order.inputs` unchanged — either way disconnected from the true balance delta when a protocol fee is also configured, since `reducedInputs` is computed from the nominal `originalAmount`, not the measured received amount.
3. Ensure at least one other legitimate order has escrowed `fotToken` in the same contract (so the pool has spare real balance to be drained from).
4. Call `cancelOrder` for the attacker's order (same-chain path). `withdraw()` transfers `body.tokens[0].amount` (built from `order.inputs`, i.e., the nominal `1000e18` or otherwise the value not reflecting fee deduction) to the attacker, funded out of the contract's pooled `fotToken` balance that includes other users' escrow.
5. Observe the attacker receiving more `fotToken` than they net-deposited, and the other user's escrow balance now insufficient to be honored on their own subsequent fill/cancel.

### Citations

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L519-530)
```text
        if (isSameChain) {
            // Same-chain: validate locally and refund immediately
            // only owner can cancel
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

            // Verify we're on the correct chain
            if (orderSource != currentChain) revert WrongChain();

            WithdrawalRequest memory body =
                WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user});

            withdraw(body, true);
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

**File:** evm/src/apps/IntentGatewayV2.sol (L282-297)
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
```
