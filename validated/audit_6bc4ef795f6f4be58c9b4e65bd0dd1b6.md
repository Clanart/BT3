## Local analog: honeypot orders in `IntentGatewayV2.withdraw` block solvers from redeeming legitimate escrow

### Title
Malicious escrowed token in an `Order.inputs` list blocks solver escrow redemption and lets the order creator reclaim everything - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.withdraw()` iterates over `body.tokens` (which comes straight from `order.inputs`, chosen entirely by the order creator at `placeOrder` time) and transfers each token to the beneficiary in a single atomic call, reverting the whole transaction if any individual transfer fails. Because `order.inputs` is attacker-controlled, a malicious order creator can escrow a legitimate token (e.g. USDC) together with a token engineered to revert on transfer. A solver who fills the order on the destination chain can never redeem the escrow on the source chain, while the creator can still cancel and reclaim the full escrow once the deadline passes.

### Finding Description
`placeOrder` lets the caller supply an arbitrary `order.inputs` array of `TokenInfo{token, amount}` that gets escrowed into `_orders[commitment][token]`: [1](#0-0) [2](#0-1) 

When the order is filled on the destination chain, the source chain later processes a `RedeemEscrow` request that calls `withdraw(body, false)`, where `body.tokens = order.inputs`, sending every escrowed token to the solver in one loop: [3](#0-2) 

Each iteration uses a low-level `.call` but still treats a failed call as fatal — `revert TransferFailed()` — so a single reverting token aborts the *entire* `withdraw` call, including the transfer of every other, legitimate token in the same list: [4](#0-3) 

The same all-or-nothing pattern exists in the non-Tron `IntentsBase._withdraw`, which uses `safeTransfer` (also reverts on failure) inside the same per-token loop: [5](#0-4) 

Attack flow:
1. Creator calls `placeOrder` with `order.inputs = [100 USDC, 1 XYZ]`, where `XYZ` is a token that reverts on `transfer` to any address it doesn't allow (e.g. reverts for everyone except the creator, or reverts unconditionally after the solver fills).
2. A solver, seeing the USDC value, fills the order on the destination chain.
3. When the `RedeemEscrow` request lands on the source chain, `withdraw(body, false)` attempts to transfer both USDC and XYZ to the solver in the same loop; the XYZ transfer reverts, so `revert TransferFailed()` unwinds the whole call and the solver receives nothing — not even the USDC.
4. Every retry of the redeem call fails identically, since the malicious token is designed to always revert to the solver.
5. After `order.deadline` passes, the creator calls `cancelOrder`, which (cross-chain) dispatches a `RefundEscrow` request whose `withdraw(body, true)` sends the same token list back to `order.user` — the creator itself: [6](#0-5) 
If the malicious token permits transfers back to the creator (trivial to arrange, since the creator deployed it), the creator recovers both the USDC and the XYZ, while the solver — who already performed the fill on the destination chain — is left with nothing.

This is the same broken invariant as the reported `IntentVault` issue: the vault's atomic "all rewards or none" transfer loop lets one poisoned asset block collection of otherwise-legitimate value, and lets the depositor reclaim everything after the fact.

### Impact Explanation
This allows an order creator to construct honeypot intents that appear profitable (legitimate token value) but are permanently uncollectible by the solver who performs real fulfillment work on the destination chain. The creator effectively steals the solver's fulfillment cost/collateral and can reclaim the full escrowed value, matching the "stealing or loss of funds" / "unauthorized transaction or execution" impact bar for this bounty. This applies to intent settlement/reward-claim logic, a core bridge-custody path.

### Likelihood Explanation
Medium: it requires an unprivileged but malicious order creator to deploy a token contract and include it in `order.inputs`. No relayer, prover, or admin compromise is needed — the creator is a normal, permissionless user of `placeOrder`, and `withdraw`'s all-or-nothing loop is unconditionally reachable through the public `RedeemEscrow`/`RefundEscrow` request-handling path (`onAccept`).

### Recommendation
Do not let a single token's transfer failure block redemption of the rest of the basket. Use low-level calls per token and record/skip failures instead of reverting the whole `withdraw`, e.g. emit a `TransferFailed` event and leave the failed token's escrow balance claimable later (or route it to a rescue/sweep path), while unconditionally paying out all tokens that transfer successfully. Apply the same fix to both `IntentGatewayV2.withdraw` (`evm/tron/contracts/apps/IntentGatewayV2.sol`) and `IntentsBase._withdraw` (`evm/src/apps/intentsv2/IntentsBase.sol`).

### Proof of Concept
1. Deploy `MaliciousToken` implementing `IERC20` whose `transfer(to, amount)` returns `true`/succeeds when `to == creator` but reverts for any other `to`.
2. Creator calls `placeOrder` with `order.inputs = [{USDC, 100e6}, {MaliciousToken, 1}]`, `order.output` describing a normal destination fill, and a reasonable `order.deadline`.
3. Solver fills the order on the destination chain per normal flow (`fillOrder`/`selectSolver`), incurring real cost.
4. Once the `RedeemEscrow` request is delivered and `withdraw(body, false)` executes on the source chain, the `MaliciousToken.transfer(solver, 1)` call reverts, causing `revert TransferFailed()` at `evm/tron/contracts/apps/IntentGatewayV2.sol:698` and rolling back the entire redemption, including the USDC transfer — verify the solver's USDC balance is unchanged and `_orders[commitment][USDC]` is unchanged.
5. After `order.deadline`, creator calls `cancelOrder`, driving the `RefundEscrow` path into `withdraw(body, true)` with `beneficiary = order.user` (the creator); `MaliciousToken.transfer(creator, 1)` succeeds, so both USDC and MaliciousToken are returned to the creator — verify the creator's USDC balance increased by 100e6 while the solver never received anything despite fulfilling the order on the destination chain.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L332-334)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable {
        // Validate that order has inputs
        if (order.inputs.length == 0) revert InvalidInput();
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L578-591)
```text
        } else if (currentChain == orderDest) {
            // destination chain: dispatch RefundEscrow request to source chain
            // If order hasn't expired, only owner can cancel
            if (order.deadline >= block.number) {
                if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
            }

            // Mark as cancelled locally to prevent fills
            _filled[commitment] = address(uint160(uint256(order.user)));

            bytes memory body = bytes.concat(
                bytes1(uint8(RequestKind.RefundEscrow)),
                abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
            );
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L394-410)
```text
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
