## Analysis

The external report's core broken invariant is: **an unprivileged actor can inflate their own on-chain accounting entry beyond what real capital they actually contributed to a shared custody pool, then redeem the inflated entry against pooled funds that belong to other participants** — the swap "executor" extracts value they never deposited.

The direct local analog exists in the Tron port of the Intent Gateway. In the canonical EVM contract, `placeOrder` explicitly measures actual received token balances to guard against fee-on-transfer/rebasing tokens (see the "Phase 1: Transfer tokens and record actual received amounts" comment and the corresponding fee-on-transfer tests). The Tron contract, which implements the identical `IntentGatewayV2` custody/settlement logic against the same single pooled ERC20 balance, is missing this guard entirely.

### Title
Fee-on-transfer / non-standard ERC20 inputs let an attacker overstate intent-order escrow and drain pooled funds belonging to other orders - (`evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.placeOrder` on the Tron contract credits the internal escrow ledger `_orders[commitment][token]` with the caller-declared `order.inputs[i].amount` (minus protocol fee), but never verifies that this amount was actually received by the contract. The canonical EVM `IntentGatewayV2.sol` measures the *actual* post-transfer balance for exactly this reason (fee-on-transfer / rebasing tokens transfer less than requested). Because all orders share one pooled ERC20 balance per token (there is no per-order token custody, only a ledger entry), an attacker who places an order using a fee-on-transfer token inflates their own ledger entry above their real contribution to the pool. When that order is filled/redeemed, `withdraw()` pays out the inflated ledger amount from the shared pool — funded by other users' real escrowed balances — without any check that the pool actually holds enough real tokens for that specific commitment.

### Finding Description
In `placeOrder` (`evm/tron/contracts/apps/IntentGatewayV2.sol:444-462`): [1](#0-0) 
```solidity
} else {
    for (uint256 i; i < inputsLen;) {
        if (order.inputs[i].amount == 0) revert InvalidInput();
        address token = address(uint160(uint256(order.inputs[i].token)));
        if (token == address(0)) {
            if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
            msgValue -= order.inputs[i].amount;
        } else {
            IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
        }
        // Store reduced amount (after protocol fees) in escrow
        _orders[commitment][token] += reducedInputs[i].amount;
        unchecked { ++i; }
    }
}
```
`reducedInputs[i].amount` is derived only from `order.inputs[i].amount` minus the protocol fee — it is never reconciled against what the contract's ERC20 balance actually increased by. For a fee-on-transfer token (or any token where `transferFrom` delivers less than requested), the contract credits the escrow ledger for tokens it never received.

Contrast with the canonical EVM contract, which explicitly measures actual received balances before crediting escrow specifically to prevent this divergence: [2](#0-1) 

All orders' tokens sit in one pooled ERC20 balance on the gateway contract — `_orders[commitment][token]` is merely a ledger, not segregated custody. `withdraw()` in the Tron contract pays out purely from the ledger with no check that the pool's real balance covers the specific commitment being redeemed: [3](#0-2) 
```solidity
function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
    address beneficiary = address(uint160(uint256(body.beneficiary)));
    _filled[body.commitment] = beneficiary;
    uint256 len = body.tokens.length;
    for (uint256 i; i < len;) {
        address token = address(uint160(uint256(body.tokens[i].token)));
        uint256 amount = body.tokens[i].amount;
        if (_orders[body.commitment][token] == 0) revert UnknownOrder();
        ...
        _orders[body.commitment][token] -= amount;
        ...
    }
}
```
The only guard is `_orders[commitment][token] != 0` — it never checks the token's real `balanceOf(address(this))`. Because the pool is fungible across all commitments, an attacker's inflated ledger entry is redeemable against tokens that other legitimate users deposited in full.

### Impact Explanation
This is direct theft of pooled bridge/escrow funds: a user can place an intent order with a fee-on-transfer ERC20 as `order.inputs`, receive a ledger credit larger than their real deposit, then have a colluding or self-controlled solver fill the order so the inflated ledger entry is redeemed via `withdraw()` — paying out real tokens that came from other users' escrow. Legitimate depositors are left unable to fully redeem their own escrow because the pool's real balance is short by exactly the amount the attacker overstated, causing either fund loss for other users or a stuck/reverting withdrawal (fund lock) once the pool is depleted.

### Likelihood Explanation
This requires only a normal, unprivileged interaction: any address can call `placeOrder` with an arbitrary ERC20 token address of their choosing (fee-on-transfer tokens are common and require no privileged access, malicious relayer, prover, or governance actor). No proof forgery, admin key, or peer compromise is needed — the flaw is a straightforward missing balance-reconciliation check in a public entrypoint that already has a fixed, documented remediation in the sibling EVM contract.

### Recommendation
Mirror the canonical EVM `IntentGatewayV2.sol` fix in the Tron contract: after `safeTransferFrom`, measure the actual balance delta received by the contract and use that (minus protocol fee) as the escrowed/committed amount, rather than trusting the caller-declared `order.inputs[i].amount`. Additionally, `withdraw()` should validate that the token's real balance is sufficient to cover the requested payout before transferring, rather than relying solely on the ledger being non-zero.

### Proof of Concept
1. Deploy/select a fee-on-transfer ERC20 `FOT` (e.g., 5% transfer fee) as an input token.
2. Attacker calls `placeOrder` with `order.inputs = [{token: FOT, amount: 1000}]`. The contract's real `FOT` balance increases by only `950` (5% fee taken), but `_orders[commitment][FOT]` is credited `~1000` (minus protocol fee), not `950`.
3. A solver (can be the attacker via a second address) fills the order; `withdraw()`/`RedeemEscrow` pays out the full ledgered amount from the gateway's pooled `FOT` balance.
4. Repeat/scale: the pool's real `FOT` balance is depleted faster than the ledger, so other users' legitimate `FOT` escrow entries can no longer be fully honored — the attacker has extracted the difference from the shared pool.

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

**File:** evm/src/apps/IntentGatewayV2.sol (L196-202)
```text
        uint256 inputsLen = order.inputs.length;

        // Phase 1: Transfer tokens and record actual received amounts.
        // For fee-on-transfer tokens, the gateway receives less than the requested amount.
        // We mutate order.inputs to reflect actual received so the commitment and escrow
        // are consistent with what the gateway holds.
        uint256 msgValue = msg.value;
```
