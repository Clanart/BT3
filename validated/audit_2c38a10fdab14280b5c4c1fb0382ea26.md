## Title
Escrow ledger credited with the unsanitized requested amount instead of the actual tokens received, enabling escrow-pool insolvency/fund theft in the Tron `IntentGatewayV2.placeOrder` - (`evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`placeOrder` in the Tron port of `IntentGatewayV2` computes the escrow-credited amount (`reducedInputs[i].amount`) from the user-supplied `order.inputs[i].amount` *before* any token transfer happens, and then credits that computed value to `_orders[commitment][token]` without ever checking how many tokens the contract actually received via `safeTransferFrom`. This is the same bug class as the reported ESMS issue: a value is "sanitized" (fee-reduced) but the sanitization is performed on the raw, attacker-controlled input rather than on the value actually realized after the critical operation, and that stale/raw-derived value is the one used for downstream fund accounting.

### Finding Description
In `placeOrder`, the protocol fee and escrow amount are derived purely from the caller-supplied `order.inputs[i].amount`: [1](#0-0) 

Then escrow crediting for the non-predispatch path does a plain `safeTransferFrom` for the same raw amount and immediately credits the *pre-computed* `reducedInputs[i].amount` to the ledger, with no reconciliation against the contract's actual token balance delta: [2](#0-1) 

Compare this to the corresponding EVM mainnet contract, which was hardened specifically against this class of bug: it snapshots the balance before `safeTransferFrom`, measures the actual delta received, and only *then* computes the protocol fee and commitment from the real received amount: [3](#0-2) 

The Tron contract never adopted this fix. For any ERC20 with a transfer fee, transfer tax, or any deviation between requested and delivered amount (common on Tron, e.g. deflationary/tax tokens), `_orders[commitment][token]` is credited with more tokens than the gateway actually holds for that specific commitment.

Because `_orders[...][token]` is a shared per-token ledger across all orders sharing that token, this doesn't just corrupt one order's own accounting — it inflates the gateway's shared IOU. This is a false-state-acceptance in the exact sense targeted by the bounty: the on-chain escrow bookkeeping in `_orders` diverges from the actual custodied balance, and the divergence is created entirely with public, unprivileged inputs (an attacker-chosen, permissionless order using a self-selected input token).

### Impact Explanation
Later, `withdraw()` pays out `body.tokens[i].amount` (the ledger-recorded, inflated amount) from the contract's pooled token balance: [4](#0-3) 

Since the contract's actual token balance is a shared pool across all outstanding commitments for that token, an attacker who places an order with a fee-on-transfer/tax token inflates their own commitment's entitlement beyond what they deposited. When that order is filled or refunded, the excess is paid out of tokens that other, unrelated users deposited for their own orders — i.e., value is siphoned from other depositors' escrow, and/or later legitimate withdrawals underflow/revert because the actual balance is insufficient to cover all outstanding ledger entries. This is a direct "loss of funds to the wrong beneficiary" / escrow-insolvency bug, matching the bounty's "stealing or loss of funds" and "false state/logic acceptance" categories, reachable by any unprivileged caller with no relayer, prover, or admin involvement.

### Likelihood Explanation
Any unprivileged user can trigger this by placing an order with `order.inputs[i].token` set to a token that doesn't deliver the full requested amount on `transferFrom` (transfer-tax tokens, deflationary/rebasing tokens, or any nonstandard ERC20 — plausible in a permissionless "any input token" design). No relayer, governance, or malicious peer is required; it is purely a same-chain call to `placeOrder`. The fix already exists and was deployed on the primary EVM contract, confirming the developers recognized and patched this exact bug class there, but the Tron variant was left unpatched.

### Recommendation
Mirror the EVM fix in the Tron contract: transfer tokens into escrow first, measure the actual balance delta received (`balanceOf` before/after `safeTransferFrom`), mutate `order.inputs[i].amount` to that measured value, and only then compute `protocolFee`/`reducedInputs`/`commitment` and credit `_orders[commitment][token]` from the *received* amount rather than the caller-supplied raw amount.

### Proof of Concept
1. Deploy/select a fee-on-transfer ERC20 `FOT` (e.g., 1% transfer tax) as an input token on Tron's `IntentGatewayV2`.
2. Attacker calls `placeOrder` with `order.inputs = [{token: FOT, amount: 1000e18}]`. `reducedInputs[0].amount` is computed as `1000e18 - protocolFee` from the raw `1000e18`, at line 357-364.
3. `safeTransferFrom(msg.sender, address(this), 1000e18)` actually delivers only `990e18` to the gateway (1% tax withheld), but `_orders[commitment][FOT] += reducedInputs[0].amount` credits based on the un-taxed `1000e18` figure (minus protocol fee), at line 453/457.
4. The gateway's `FOT` balance increased by only `990e18`, but its ledger now believes this commitment is entitled to `~1000e18 - fee`, which is more than was actually deposited — the excess is backed by other orders' `FOT` deposits held in the same pooled balance.
5. When this order (or another legitimate `FOT` order sharing the pool) is filled/cancelled via `withdraw()`, the shortfall manifests as either an over-payment to the attacker's beneficiary (funds effectively taken from other depositors) or a revert/underflow for the last party to withdraw, denying them their rightful escrow.

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

**File:** evm/src/apps/IntentGatewayV2.sol (L281-331)
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

        // Phase 2: Compute protocol fees and commitment from actual received amounts.
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
                if (originalAmount == 0) revert InvalidInput();
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
                address token = address(uint160(uint256(order.inputs[i].token)));

                if (protocolFee > 0) emit DustCollected(token, protocolFee);

                reducedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: reducedAmount});
                unchecked {
                    ++i;
                }
            }

            order.inputs = reducedInputs;
            commitment = keccak256(abi.encode(order));
        } else {
            reducedInputs = order.inputs;
            commitment = keccak256(abi.encode(order));
        }
```
