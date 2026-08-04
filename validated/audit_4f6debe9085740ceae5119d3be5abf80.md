## Finding: Tron `IntentGatewayV2.placeOrder` credits escrow from the nominal input amount instead of the actual received balance

The external 1inch Farming report's core defect — accounting for token amounts by the *requested* transfer value instead of the *actually received* balance — has a direct, currently unpatched analog in this repo's Tron variant of the Intent Gateway.

### Title
Escrow accounting in Tron `IntentGatewayV2.placeOrder` ignores fee-on-transfer deductions, causing insolvent/commingled escrow and fund loss - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The main EVM `IntentGatewayV2.placeOrder` (`evm/src/apps/IntentGatewayV2.sol`) was hardened to measure the *actual* token balance the gateway receives before crediting escrow, explicitly to defend against fee-on-transfer tokens: [1](#0-0) 

It does this via balance-before/balance-after snapshots on every input transfer: [2](#0-1) 

The Tron fork of the same contract, `evm/tron/contracts/apps/IntentGatewayV2.sol`, does **not** carry this fix. It computes the reduced (post-protocol-fee) escrow amount straight from the caller-supplied nominal `order.inputs[i].amount`, then blindly calls `safeTransferFrom` for that same nominal amount without ever checking what the gateway actually received: [3](#0-2) [4](#0-3) 

Escrow bookkeeping (`_orders[commitment][token] += reducedInputs[i].amount`) is therefore based on the *requested* amount, not the tokens the contract physically holds.

### Finding Description
For a TRC-20 token that deducts a fee on transfer (e.g. certain stablecoin implementations), `IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount)` moves less than `order.inputs[i].amount` into the gateway, but the code still records `_orders[commitment][token] = reducedInputs[i].amount` (derived from the full nominal amount minus only the protocol fee). The contract's internal ledger for that commitment is now larger than the tokens it actually holds for that token.

Because all orders for the same token share one physical ERC20 balance on the gateway contract, this deficit is not scoped to the single affected order — it is drawn from the shared pool. The predispatch/calldata branch has the identical defect: the escrow credit still uses `reducedInputs[i].amount` (nominal-derived) rather than the dispatcher's measured post-sweep balance: [5](#0-4) 

`withdraw()` (invoked from `onAccept` for `RedeemEscrow`/`RefundEscrow`, and from same-chain `cancelOrder`) pays out exactly the amount recorded/claimed in the request, gated only by a non-zero check, not by any actual-balance verification: [6](#0-5) 

So whichever commitment redeems first for a given fee-on-transfer token is paid in full out of the commingled balance — at the expense of other orders' escrow for that same token, whose subsequent withdrawals then revert (`TransferFailed()`) because the physical balance has already been drained below what their `_orders[...]` entries claim.

### Impact Explanation
This is an unprivileged, purely on-chain accounting corruption reachable by any user who places an order using a fee-on-transfer token:
- Loss/lock of funds: legitimate users' escrowed principal for the same token can become unrecoverable once earlier redemptions have consumed the shared balance.
- Effective fund theft: an order placed with a FOT token records more escrow than was actually deposited; when that order is filled/redeemed, it is paid from tokens that rightfully back *other* users' orders, since the ledger and physical balance have desynced.
- No malicious relayer, prover, or governance actor is required — the deficiency is purely in `placeOrder`'s bookkeeping.

### Likelihood Explanation
Requires only that some TRC-20 token integrated with the gateway implements a transfer fee (common for certain stablecoin/compliance-token designs on Tron, mirroring the USDT-style fee-on-transfer scenario from the original report) and that a user places an order in that token. No special privileges or timing dependencies are needed, making this straightforward to trigger by any ordinary user.

### Recommendation
Port the same fix already present in `evm/src/apps/IntentGatewayV2.sol` into `evm/tron/contracts/apps/IntentGatewayV2.sol`: snapshot the gateway's (and, for predispatch, the dispatcher's) token balance before and after each transfer, mutate `order.inputs[i].amount` to the actually-received delta, and derive `reducedInputs`/escrow credits from that measured amount rather than the caller-supplied nominal amount.

### Proof of Concept
1. Deploy a TRC-20 token with e.g. a 1% fee-on-transfer.
2. Have user A call `placeOrder` with `inputs[0] = {token: FOT, amount: 1000}`. The gateway physically receives only 990 tokens, but `_orders[commitmentA][FOT]` is set to `1000 * (1 - protocolFeeBps)` — i.e., calculated from 1000, not 990.
3. Have user B similarly place another order using the same FOT token, e.g. `inputs[0] = {token: FOT, amount: 1000}` → gateway physically holds `990 (from A) + 990 (from B) = 1980`, but ledger entries sum to `~1000 + ~1000` (minus protocol fee) — i.e., the ledger total now exceeds the physical balance by roughly the cumulative transfer-fee amount.
4. Fill/redeem order A fully (`withdraw` pays out the full ledgered `_orders[commitmentA][FOT]` amount using `.call`/`transfer`, drawing on the commingled pool that also contains B's escrow).
5. When order B is later redeemed or cancelled, `withdraw()`'s `token.call(...transfer...)` fails because the physical FOT balance has already been depleted below what `_orders[commitmentB][FOT]` promises, reverting with `TransferFailed()` — user B's funds are stuck/lost while the shortfall was effectively transferred out via order A's redemption.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L198-201)
```text
        // Phase 1: Transfer tokens and record actual received amounts.
        // For fee-on-transfer tokens, the gateway receives less than the requested amount.
        // We mutate order.inputs to reflect actual received so the commitment and escrow
        // are consistent with what the gateway holds.
```

**File:** evm/src/apps/IntentGatewayV2.sol (L288-292)
```text
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
                }
```

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
