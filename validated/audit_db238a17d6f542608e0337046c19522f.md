## Title
Escrow ledger credited with pre-transfer amount, not actual received balance, in Tron `IntentGatewayV2.placeOrder` — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron variant of `IntentGatewayV2.placeOrder` credits the shared escrow accounting map `_orders[commitment][token]` with the *requested* (pre-fee-on-transfer) input amount instead of the amount actually received by the contract. The mainline EVM contract (`evm/src/apps/IntentGatewayV2.sol`) explicitly guards against this by snapshotting `balanceOf` before and after `safeTransferFrom` and mutating `order.inputs[i].amount` to the real delta [1](#0-0) , but the Tron port omits that measurement and simply calls `safeTransferFrom` then credits the nominal, un-discounted amount [2](#0-1) .

### Finding Description
In `placeOrder`, for non-predispatch orders, the Tron contract does:
```
IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
_orders[commitment][token] += reducedInputs[i].amount;
```
`reducedInputs[i].amount` is derived purely from `order.inputs[i].amount` minus the protocol fee — it never reflects what the contract's ERC20 balance actually increased by [3](#0-2) . For a fee-on-transfer/deflationary token, `transferFrom` moves less than `order.inputs[i].amount` into the contract, yet the escrow ledger records the full nominal amount as if it were received.

`_orders` is a shared token balance pool across *all* commitments — the contract does not segregate real ERC20 balance per commitment. When settlement runs, `withdraw()` unconditionally transfers the recorded (uncorrected, inflated) amount out of the shared pool:
```
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
_orders[body.commitment][token] -= amount;
``` [4](#0-3) 

Because the ledger overstates what the contract actually holds for that token, an attacker can place an order using a fee-on-transfer ERC20 as an input (there is no token allowlist/registration check in `placeOrder` — any `order.inputs[i].token` address is accepted). Their commitment is credited with the full nominal amount even though the real balance increase was smaller. On settlement (via `onAccept`/`withdraw`, reachable through the normal `RedeemEscrow`/`RefundEscrow` flow, or the same-chain cancel/fill paths), the beneficiary is paid the inflated recorded amount, which is only payable because the shared token pool contains real balance contributed by *other* users' unrelated escrowed orders in that same token. This drains value that belongs to other users' orders, or later withdrawals for that token simply revert (fund lock) once the pool is depleted.

### Impact Explanation
This is a direct fund-loss / wrong-amount vector: an unprivileged user can, via a single `placeOrder` call using any fee-on-transfer ERC20 as input, cause the contract's internal accounting to promise more tokens than it actually escrowed for that commitment. Settlement then pays out the inflated amount from the shared per-token balance pool, effectively taking real principal that other users deposited for their own orders. This matches the bounty's "stealing or loss of funds" and "wrong beneficiary or amount" impact categories, and is analogous to the fee-on-transfer/rebasing-token class from the external report, but concretely realized here as an escrow-pool insolvency/theft primitive rather than a simple balance mismeasurement.

### Likelihood Explanation
Likelihood is moderate: it requires the deployment to accept an ERC20 that applies a transfer fee (deflationary/fee-on-transfer token) as an order input, which the contract does not prevent — there is no token registry check in `placeOrder`. Any user can supply such a token address for their own order; they don't need to be a relayer, prover, or admin. The mainline EVM contract in the same repository demonstrates the team is aware of and has already patched this exact class of bug, making its absence in the Tron port a genuine regression rather than a hypothetical.

### Recommendation
Apply the same balance-delta measurement used in `evm/src/apps/IntentGatewayV2.sol` to the Tron contract: snapshot `IERC20(token).balanceOf(address(this))` before and after `safeTransferFrom`, and credit `_orders[commitment][token]` with the actual received delta rather than the nominal requested amount, for both the predispatch and non-predispatch escrow paths.

### Proof of Concept
1. Deploy (or attacker-supplied) a fee-on-transfer ERC20 token `T` with e.g. a 10% transfer fee, and have it added as a legitimate input token used by multiple users (no allowlist prevents this).
2. Attacker calls `placeOrder` with `order.inputs = [{token: T, amount: 1000}]`. `safeTransferFrom` moves only 900 `T` into the contract (100 burned/fee), but `_orders[commitment][T]` is credited with `reducedInputs[i].amount` computed from the nominal `1000` (minus protocol fee), i.e. an amount larger than the 900 actually held.
3. Other legitimate users place orders using the same token `T`, correctly contributing real balance into the shared pool.
4. When attacker's order is settled/cancelled, `withdraw()` transfers the inflated recorded amount to the attacker's beneficiary from the shared token balance, funded by the real deposits of other users' orders in `T`.
5. Later legitimate withdrawals for token `T` either receive less than owed or revert with `TransferFailed()` once the pool is depleted, demonstrating fund loss/lock for other users caused entirely by the attacker's unprivileged `placeOrder` call.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L288-292)
```text
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
                }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L350-379)
```text
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L445-462)
```text
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L688-705)
```text
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
