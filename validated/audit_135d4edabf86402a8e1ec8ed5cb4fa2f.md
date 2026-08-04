## Title
Withdraw reverts on revert-on-zero-transfer input tokens, permanently freezing escrowed order funds — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
`IntentGatewayV2.withdraw()` on the Tron variant of the IntentGateway loops over an order's escrowed input tokens and unconditionally attempts a `transfer`/native-call for every entry, with no `amount == 0` skip. This is the exact bug class from the referenced report: a token that reverts on zero-value transfers (a real, documented ERC-20 quirk) will make the whole `withdraw()` call revert whenever one of the escrowed token legs has a zero amount, and `withdraw()` is the only path (`RedeemEscrow`/`RefundEscrow`) that releases escrow. Notably, the sibling EVM implementation (`evm/src/apps/intentsv2/IntentsBase.sol`) already guards this exact case with `if (amount == 0) continue;`, showing the project is aware of and mitigates this hazard elsewhere — the Tron contract appears to have missed that fix.

### Finding Description
`withdraw()` in `evm/tron/contracts/apps/IntentGatewayV2.sol` (lines 682–705):

```solidity
function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
    address beneficiary = address(uint160(uint256(body.beneficiary)));
    _filled[body.commitment] = beneficiary;

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
        unchecked { ++i; }
    }
    ...
}
```

There is no `if (amount == 0) continue;` guard, unlike the equivalent function in `evm/src/apps/intentsv2/IntentsBase.sol` (lines 394–410), which explicitly does:

```solidity
uint256 amount = body.tokens[i].amount;
if (amount == 0) continue;
```

`withdraw()` is invoked from `onAccept()` for both `RequestKind.RedeemEscrow` and `RequestKind.RefundEscrow` (lines 620–626), which are the only mechanisms to release escrowed order inputs back to a beneficiary (solver on fill, or user on cancellation/refund). The `body.tokens` array comes directly from `order.inputs`, which the order creator fully controls at `placeOrder()` time (line 332 onward) — a multi-token order can include a leg with `amount == 0` for a token, or a protocol-fee-reduced amount that nets to zero (`reducedAmount = originalAmount - protocolFee`).

If any one of the tokens in the order's `inputs` array is a token that reverts on a zero-value `transfer` (a well-documented real-world ERC-20 behavior, e.g. weird-erc20's "revert on zero value transfers" category — same class cited in the reference report), and that leg's escrowed/withdrawal amount is zero, the `token.call(...)` still executes the `transfer(beneficiary, 0)` call. If the token reverts on that zero-value transfer, `success` is `false` and the function reverts with `TransferFailed()` — aborting the entire withdrawal, including the release of every other (non-zero, healthy) escrowed token in the same order.

### Impact Explanation
Because `withdraw()` is the sole redemption path for escrowed order funds (`RedeemEscrow` for successful fills, `RefundEscrow` for cancellations), a single reverting zero-amount leg permanently locks all escrowed assets tied to that commitment — this satisfies the bounty's fund-loss/fund-lock impact category. Every retry of `onAccept` for the same commitment will fail identically since the zero-amount condition (protocol fee fully consuming the input, or a user-specified zero leg) does not change over time. This is not dependent on a malicious relayer, prover, or governance actor — it only requires an ordinary order creator to include a zero-value/fully-fee-consumed leg denominated in a revert-on-zero-transfer token, which is a legitimate, permissionless, unprivileged action via `placeOrder()`.

### Likelihood Explanation
Likelihood is moderate: it requires (a) a token used as an order input that reverts on zero-value transfers, and (b) a resulting zero amount for that leg at settlement time (either the user directly sets `amount = 0` for one leg of a multi-token order, or the protocol fee computation fully consumes a small `originalAmount`, e.g. `originalAmount * protocolFeeBps / 10_000 == originalAmount` for tiny amounts). No adversarial relayer, prover, or governance behavior is needed — a normal user/solver interaction can trigger it, and once triggered it is a persistent DoS/fund lock on that specific order's escrow.

### Recommendation
Mirror the guard already present in `evm/src/apps/intentsv2/IntentsBase.sol`: skip the transfer entirely when `amount == 0` in `IntentGatewayV2.withdraw()`:

```solidity
uint256 amount = body.tokens[i].amount;
if (amount == 0) { unchecked { ++i; } continue; }
if (_orders[body.commitment][token] == 0) revert UnknownOrder();
...
```
Apply the same treatment to any other loops in this contract handling per-token transfers driven by user-supplied amounts (e.g., dust-sweep and fee transfer paths), and audit other places outside `IntentsBase.sol`/`IntentGatewayV2.sol` where multi-token payout loops require every transfer to succeed.

### Proof of Concept
1. Deploy/select an ERC-20 token `Z` that reverts on `transfer(to, 0)` (a real behavior per weird-erc20's "revert on zero value transfers").
2. Call `placeOrder()` with an order whose `inputs` include a leg `{token: Z, amount: X}` where `X` is small enough that, after protocol-fee reduction, `reducedAmount = X - (X * protocolFeeBps / 10_000)` truncates to `0` (or simply include a second leg with `amount = 0` directly, since `placeOrder()` does not reject zero-amount input legs).
3. The order proceeds normally; escrow is recorded in `_orders[commitment][Z]` (possibly `0` for that leg, non-zero for other legs).
4. On fill/cancel, the counterparty side dispatches a `RedeemEscrow`/`RefundEscrow` request back to this chain's `IntentGatewayV2`, invoking `onAccept -> withdraw(body, ...)`.
5. In the loop, for token `Z` with `amount == 0`, `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, 0))` reverts inside `Z`, causing `success == false`.
6. `withdraw()` reverts with `TransferFailed()`, and since this is the only redemption path for the commitment, all tokens escrowed under that order (including unrelated, healthy tokens in the same multi-token order) remain permanently locked in the contract. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L332-379)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable {
        // Validate that order has inputs
        if (order.inputs.length == 0) revert InvalidInput();

        address hostAddr = host();
        // fill out the order preludes
        order.user = bytes32(uint256(uint160(msg.sender)));
        order.source = IDispatcher(hostAddr).host();
        order.nonce = _nonce++;

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L620-626)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return withdraw(body, kind == RequestKind.RefundEscrow);
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L391-410)
```text
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
