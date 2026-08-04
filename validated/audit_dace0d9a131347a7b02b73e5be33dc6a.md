## Analysis

I found a direct structural analog to the TokenFactory bug in Hyperbridge's intent-settlement path. The core broken invariant in the original report is: **an attacker-controlled recipient address embedded in a shared, permissionless execution path can force a revert, letting that attacker unilaterally decide whose calls succeed.** The same pattern exists in `IntrinsicIntents._fillSameChain`.

### Title
Malicious order creator can selectively block competing solvers from filling an order via a reverting native-ETH beneficiary - (`evm/src/apps/intentsv2/IntrinsicIntents.sol`)

### Summary
When placing a same-chain intent order, the order creator freely chooses `order.output.beneficiary` — the address that receives the solver's output payment. `_fillSameChain` pushes native ETH to this beneficiary with a raw `call` and reverts the entire fill transaction if the push fails [1](#0-0) . Because the beneficiary is an arbitrary contract chosen by the order creator, its `receive()` function can implement caller-dependent logic (e.g. checking `tx.origin`) to accept ETH from a favored, colluding solver while reverting for every other solver that attempts to fill the same order.

### Finding Description
`_fillSameChain` is a permissionless entry point: any solver can call it to fill an order and claim the escrowed input tokens [2](#0-1) . For each output asset, if the asset is native ETH, the function performs:
```solidity
(bool sent,) = beneficiary.call{value: beneficiaryTotal}("");
if (!sent) revert InsufficientNativeToken();
``` [3](#0-2) 

`beneficiary` is `address(uint160(uint256(order.output.beneficiary)))`, taken directly from the order struct that the order creator fully controls at `placeOrder` time [4](#0-3) . Nothing constrains this address to be an EOA or a well-behaved contract, and the revert-on-failure design (mirroring `_withdraw`'s `if (!sent) revert InsufficientNativeToken();` pattern used throughout `IntentsBase.sol` [5](#0-4) ) means any solver whose fill attempt triggers a revert in `beneficiary`'s fallback loses the entire transaction — the input-escrow release to that solver, and the fill itself, never happen because `_withdraw` is only reached after the output loop succeeds.

Since the beneficiary contract is a normal contract call, its `receive()`/`fallback()` can execute arbitrary logic, including reading `tx.origin` (the solver's EOA, since the call to `beneficiary` originates from the gateway contract, but `tx.origin` still identifies the ultimate solver-initiated transaction) and deciding to `revert()` for any solver other than one the order creator wants to win the fill. This lets a malicious order creator:
- Force all "honest" solvers' fill attempts to revert (wasting their gas),
- Guarantee that only a colluding solver's fill succeeds,
thereby controlling who is allowed to fill the order — the same "malicious creator controls who can transact" primitive as the original TokenFactory report, just relocated from a bonding-curve buy/sell path to the intents fill path.

### Impact Explanation
This breaks the permissionless, first-come/competitive nature of solver fills that the intents system relies on for fair price discovery. A malicious order creator can:
- Grief arbitrary solvers' gas by making their fills always revert.
- Guarantee a colluding solver is the only one who can ever successfully fill the order, letting that colluding party control fill timing/pricing without open competition.
This is a logic attack on the intent-settlement path's fairness/availability guarantees, exploitable by any unprivileged order creator with no need for a malicious relayer, prover, or admin.

### Likelihood Explanation
Likelihood is high: placing an order with a crafted `output.beneficiary` contract requires no special privilege, and the discriminating revert logic (checking `tx.origin` against an allow-list of one colluding solver) is trivial to implement in Solidity. The attack surface is exposed on every same-chain order with a native-ETH output asset.

### Recommendation
Do not let a failed push-transfer to `beneficiary` abort solver-fill logic that other solvers depend on for fair, permissionless access. Options:
1. Use a pull-based withdrawal pattern for native-ETH outputs (credit an internal balance for `beneficiary` and let them withdraw separately) instead of a push `call` inside the shared fill path.
2. If a push must be used, cap the gas forwarded and treat a failed push as a recoverable event (e.g., stash the funds for later claim) rather than reverting the entire fill, so the escrow release and solver accounting proceed regardless of the beneficiary's behavior.
3. Alternatively, require `order.output.beneficiary` to be validated (e.g., disallow contracts, or require a successful test transfer) at order-placement time so orders with pathological beneficiaries cannot be placed in the first place.

### Proof of Concept
1. Attacker deploys `MaliciousBeneficiary` with:
```solidity
contract MaliciousBeneficiary {
    address colludingSolver;
    receive() external payable {
        if (tx.origin != colludingSolver) revert();
    }
}
```
2. Attacker calls `placeOrder` with `order.output.beneficiary = address(MaliciousBeneficiary)` and a native-ETH output asset.
3. Any honest solver calling into the fill path that ultimately invokes `_fillSameChain` (see `evm/src/apps/intentsv2/IntrinsicIntents.sol:104-105`) has their transaction revert, losing gas and never filling the order.
4. Only a transaction whose `tx.origin == colludingSolver` succeeds in pushing ETH to the beneficiary, allowing that solver's fill (and only that fill) to complete — matching the "malicious creator can block/allow selectively" pattern from the source report. [6](#0-5)

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L54-149)
```text
    function _fillSameChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
        bool isFullyFilled = true;

        TokenInfo[] memory escrowedInputs = new TokenInfo[](outputsLen);
        TokenInfo[] memory outputFills = new TokenInfo[](outputsLen);

        for (uint256 i; i < outputsLen; i++) {
            bytes32 outputToken = order.output.assets[i].token;
            if (options.outputs[i].token != outputToken) revert InvalidInput();

            address token = address(uint160(uint256(outputToken)));
            uint256 totalRequired = order.output.assets[i].amount;
            uint256 solverAmount = options.outputs[i].amount;

            uint256 alreadyFilled = _partialFills[commitment][outputToken];
            uint256 remaining = totalRequired - alreadyFilled;
            if (remaining == 0 || solverAmount == 0) {
                if (solverAmount == 0 && remaining > 0) isFullyFilled = false;
                continue;
            }
            uint256 fillAmount;

            uint256 beneficiaryShare = 0;
            uint256 protocolShare = 0;
            if (alreadyFilled == 0 && solverAmount > totalRequired) {
                fillAmount = totalRequired;
                uint256 dust = solverAmount - totalRequired;
                if (order.output.call.length > 0) {
                    protocolShare = dust;
                } else {
                    protocolShare = (dust * _params.surplusShareBps) / 10_000;
                    beneficiaryShare = dust - protocolShare;
                }
            } else {
                fillAmount = solverAmount > remaining ? remaining : solverAmount;
            }

            uint256 amountFilled = alreadyFilled + fillAmount;
            _partialFills[commitment][outputToken] = amountFilled;
            uint256 beneficiaryTotal = fillAmount + beneficiaryShare;

            if (token == address(0)) {
                if (msgValue < beneficiaryTotal + protocolShare) revert InsufficientNativeToken();
                msgValue -= (beneficiaryTotal + protocolShare);
                (bool sent,) = beneficiary.call{value: beneficiaryTotal}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransferFrom(msg.sender, beneficiary, beneficiaryTotal);
                if (protocolShare > 0) {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), protocolShare);
                }
            }

            if (totalRequired > amountFilled) isFullyFilled = false;
            if (protocolShare > 0) emit DustCollected(token, protocolShare);

            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
            escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
            outputFills[i] = TokenInfo({token: outputToken, amount: fillAmount});
        }

        // Orders carrying output calldata must be filled completely in a single fill.
        // The attached call is only executed on a full fill, so a partial fill would
        // leave the intended side effect unexecuted while releasing proportional escrow.
        if (order.output.call.length > 0 && !isFullyFilled) revert PartialFillNotAllowed();

        WithdrawalRequest memory body = WithdrawalRequest({
            commitment: commitment, tokens: escrowedInputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
        });
        _withdraw(body, false, isFullyFilled);

        if (isFullyFilled) {
            _execute(order, outputsLen);
            emit OrderFilled({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
        } else {
            delete _filled[commitment];
            emit PartialFill({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
        }

        // Refund any unspent native tokens to the solver.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L404-406)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
```
