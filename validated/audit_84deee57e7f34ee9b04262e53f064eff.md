## Title
Fee-on-transfer/short-transfer ERC20 input tokens create phantom escrow balances that drain honest users' funds in the Tron `IntentGatewayV2` — (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

## Summary
The main EVM `IntentGatewayV2.placeOrder()` was hardened against the "arbitrary reward token" bug class described in the external report: it measures the gateway's actual token balance before and after `safeTransferFrom` and mutates `order.inputs[i].amount` to the amount *actually received*, so the escrow bookkeeping (`_orders[commitment][token]`) always matches real holdings. The Tron variant of the same contract, `evm/tron/contracts/apps/IntentGatewayV2.sol`, does not carry this fix: it calls `safeTransferFrom` for the requested amount and then unconditionally credits `_orders[commitment][token] += reducedInputs[i].amount` — the full nominal (fee-reduced) amount — regardless of what the gateway actually received.

## Finding Description
In the reference (fixed) implementation, `placeOrder()` snapshots balances and derives the escrowed amount from the delta: [1](#0-0) 

This ensures `order.inputs[i].amount` (and therefore the commitment and the escrow ledger) reflects tokens actually held by the contract, protecting against fee-on-transfer or non-standard ERC20s that deliver less than the requested amount.

The Tron deployment of the identical protocol lacks this balance measurement. It transfers the nominal amount and then credits the ledger with the nominal (fee-adjusted) amount without ever checking what arrived: [2](#0-1) 

Because `order.inputs` in `placeOrder()` (and the resulting `Order` struct broadcast in `OrderPlaced`/used to build settlement/withdrawal messages) is never adjusted to actual-received, `_orders[commitment][token]` is a purely nominal accounting figure that can permanently exceed the contract's real token balance whenever the input token charges a transfer fee, taxes transfers, or otherwise delivers less than requested (`transferFrom` returning success while moving less — a known weird-ERC20 pattern flagged in the original report).

Both settlement paths pay out strictly from this nominal ledger without ever verifying actual on-hand balance:
- Same-chain / cross-chain redemption (`withdraw`) transfers `body.tokens[i].amount` and decrements `_orders[commitment][token]` purely from bookkeeping: [3](#0-2) 

Since every order commitment shares the same underlying token contract balance pool, once one order's nominal escrow exceeds what the gateway actually holds for that token (due to a short/fee-on-transfer transfer), a solver/user who is first to redeem drains real balance that "on paper" was reserved for a different, legitimate commitment. Later, equally valid solvers/users attempting to redeem their own separately-tracked (but now under-collateralized in aggregate) escrow for the same token will have their ERC20 `transfer` fail for insufficient balance, reverting their withdrawal and locking/denying their rightful funds — the exact "drain/deny" outcome described in the external report, just moved from the reward-token leg to the input-escrow leg, and on the Tron deployment target instead of the primary EVM one.

## Impact Explanation
This is a genuine loss/denial-of-funds path reachable by any unprivileged user simply by choosing (or organically using) a fee-on-transfer/short-transfer ERC20 as an order input token in `placeOrder()` — no malicious relayer, prover, or admin is required. The result is fund loss or fund-lock for other, unrelated order participants sharing the same token, which matches the bounty's "stealing or loss of funds" / "logic attack" impact categories.

## Likelihood Explanation
Medium: it requires the gateway to be configured to accept a fee-on-transfer or non-standard-transfer ERC20 as an input token on the Tron deployment. Since the protocol description states it intentionally stays permissionless/open with respect to token choice (per the "Eco" response in the original report acknowledging no whitelist), any user can trigger this simply by placing an order with such a token — it does not depend on any other actor's cooperation.

## Recommendation
Port the balance-delta measurement from `evm/src/apps/IntentGatewayV2.sol` (lines 282–298) into `evm/tron/contracts/apps/IntentGatewayV2.sol`: after each `safeTransferFrom` in `placeOrder`, measure `IERC20(token).balanceOf(address(this))` before and after, and use the actual received delta (net of any predispatch flow) to compute `reducedInputs[i].amount`/`_orders[commitment][token]`, so escrow accounting can never exceed real token custody.

## Proof of Concept
1. Gateway operator adds/permits a fee-on-transfer ERC20 `FOT` (e.g., 5% fee on transfer) as a valid input token on the Tron `IntentGatewayV2`.
2. Alice calls `placeOrder` with `order.inputs = [{token: FOT, amount: 100}]`. `safeTransferFrom` moves 100 nominal FOT, but the gateway actually receives only 95 due to the transfer fee. The Tron contract nonetheless sets `_orders[commitmentA][FOT] = 100` (minus any protocol fee) — see `evm/tron/contracts/apps/IntentGatewayV2.sol:453-457`.
3. Bob independently places another order also using FOT as input, likewise crediting `_orders[commitmentB][FOT]` with its full nominal amount; the gateway's real FOT balance is the sum of all actual (fee-reduced) receipts, strictly less than the sum of all nominal escrow entries.
4. When solvers fill Alice's and Bob's orders and the redeem/refund path calls `withdraw()` (`evm/tron/contracts/apps/IntentGatewayV2.sol:682-705`), the first redemption succeeds by consuming real FOT balance that on paper belonged partly to the other commitment's escrow.
5. The second, equally legitimate redemption's `token.call(...transfer...)` returns `false` (insufficient real balance), causing `revert TransferFailed()` — the rightful solver/user's escrowed funds are denied/locked, even though their `_orders` entry still shows a nonzero balance. [2](#0-1) [3](#0-2) [1](#0-0)

### Citations

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
