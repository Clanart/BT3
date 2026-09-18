### Title
Excess `msg.value` sent to `MultiSender.batchTransfer`/`batchTransferEqualAmount` is never refunded and becomes permanently stuck in the contract - (File: contracts/src/MultiSender.sol)

### Summary
`MultiSender.sol` exposes two `payable` batch-send functions (`batchTransferEqualAmount` and `batchTransfer`) that only require `msg.value >= totalAmount` but never return the difference between `msg.value` and `totalAmount` to the caller, and the contract has no withdrawal function for the excess. Any usei/wei sent beyond the exact required total is permanently locked in the contract.

### Finding Description
Both entry points compute a `totalAmount` from the caller-supplied recipients/amounts and only assert that `msg.value` is sufficient: [1](#0-0) [2](#0-1) 

In both functions, the code loops over recipients using `payable(recipients[i]).send(amount)`/`send(amounts[i])`, sending only `totalAmount` in aggregate, while any surplus (`msg.value - totalAmount`) remains held by the `MultiSender` contract's balance. There is no refund-to-sender logic (unlike patterns seen elsewhere in the repo, e.g. `swapETHForExactTokens` in `UniswapV2Router02.sol` which explicitly does `if (msg.value > amounts[0]) TransferHelper.safeTransferETH(msg.sender, msg.value - amounts[0]);`), and `MultiSender.sol` defines no `withdraw`/`sweep`/`receive` recovery mechanism at all. This is the exact bug class described in the referenced report: tokens/native currency provided in excess of what is actually consumed by the intended operation are silently retained by the contract instead of being returned to the depositor.

### Impact Explanation
Any unprivileged EVM account that calls `batchTransferEqualAmount` or `batchTransfer` with `msg.value` greater than the exact sum required (e.g., due to a rounding/estimation mistake, a wallet's built-in slippage buffer, or reusing a prior calldata/value pairing) permanently loses the difference — it becomes stuck usei/wei with no owner, admin, or user-facing way to recover it. This is a concrete, irreversible loss of user funds reachable purely through a standard EVM contract call, matching the accepted "concrete fund loss" impact category.

### Likelihood Explanation
Likelihood is high for accidental triggering: any integrator or user who over-provisions `msg.value` (a common practice to guard against gas/price estimation errors, or simply passing a slightly larger amount) will lose the excess with 100% certainty and no warning, since the call succeeds without reverting. No special privileges, races, or malicious counterparties are required — a single ordinary transaction reproduces the loss deterministically.

### Recommendation
After completing the batch sends, compute `msg.value - totalAmount` and refund the remainder to `msg.sender` (e.g., via a `call`/`send` with a `require` check), mirroring the refund pattern already used elsewhere in the codebase (`UniswapV2Router02.sol`'s dust-refund logic). Alternatively, require `msg.value == totalAmount` exactly and revert otherwise, or add a rescue/sweep function restricted appropriately.

### Proof of Concept
1. Deploy `MultiSender` (or use the existing deployed instance).
2. Call `batchTransferEqualAmount(recipients, amount)` with `msg.value = amount * recipients.length + X` for some `X > 0`.
3. Observe: all recipients receive `amount` each (total spent = `amount * recipients.length`), the transaction succeeds, and `X` wei remains in the `MultiSender` contract's balance with no way for the caller (or anyone) to retrieve it.
4. Repeat with `batchTransfer` supplying `msg.value` above `sum(amounts)` — the identical loss occurs.

### Citations

**File:** contracts/src/MultiSender.sol (L11-23)
```text
  function batchTransferEqualAmount(
    address[] calldata recipients,
    uint256 amount
  ) external payable {
    uint256 totalAmount = amount * recipients.length;
    require(msg.value >= totalAmount, "Insufficient amount sent");

    for (uint256 i = 0; i < recipients.length; i++) {
      bool success = payable(recipients[i]).send(amount);
      require(success, "Failed to send Ether");
      emit SendSuccessful(msg.sender, recipients[i], amount);
    }
  }
```

**File:** contracts/src/MultiSender.sol (L25-45)
```text
  function batchTransfer(
    address[] calldata recipients,
    uint256[] calldata amounts
  ) external payable {
    require(
      recipients.length == amounts.length,
      "Recipients and amounts do not match"
    );
    uint256 totalAmount = 0;
    for (uint256 i = 0; i < amounts.length; i++) {
      totalAmount += amounts[i];
    }

    require(msg.value >= totalAmount, "Insufficient amount sent");

    for (uint256 i = 0; i < recipients.length; i++) {
      bool success = payable(recipients[i]).send(amounts[i]);
      require(success, "Failed to send Ether");
      emit SendSuccessful(msg.sender, recipients[i], amounts[i]);
    }
  }
```
