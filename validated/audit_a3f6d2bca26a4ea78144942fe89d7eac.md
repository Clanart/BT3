### Title
Loss of funds to msg.sender from missing excess Sei refund in `MultiSender.batchTransferEqualAmount()`/`batchTransfer()` - (File: contracts/src/MultiSender.sol)

### Summary
`MultiSender.sol`'s `batchTransferEqualAmount()` and `batchTransfer()` are `payable` functions that only require `msg.value >= totalAmount` before distributing `totalAmount` worth of native Sei to the recipient list. Neither function refunds the caller for `msg.value - totalAmount`, so any overpayment is permanently stranded in the contract with no withdrawal path.

### Finding Description
Both batch-send functions compute a required total (`amount * recipients.length` or `sum(amounts)`) and gate execution with a `>=` check rather than an exact-match check: [1](#0-0) [2](#0-1) 

After the `require(msg.value >= totalAmount, ...)` check passes, the loop only ever `send()`s exactly `totalAmount` (or the summed `amounts`) to the recipients — the surplus `msg.value - totalAmount` remains held by the `MultiSender` contract. There is no refund-to-`msg.sender` step and no `withdraw`/rescue function anywhere in the contract, so the excess Sei is permanently unrecoverable by the caller (or anyone else). This is structurally identical to the reported Footium `mintPlayers()` bug: a fee-like required amount is validated with `>=` instead of `==`/exact-refund logic, and the delta is silently absorbed by the contract instead of returned to `msg.sender`.

### Impact Explanation
Any unprivileged EVM transaction sender who calls `batchTransferEqualAmount` or `batchTransfer` with `msg.value` greater than the computed total (e.g., due to a client-side estimation error, a slightly stale price, or intentionally testing tolerance) loses the difference permanently — the funds become stuck in the contract with no owner-only or public withdrawal mechanism. This is a direct, concrete loss of user funds via a public-facing contract deployed as part of this repo's Solidity contract set, matching the High-severity classification of the analog report (loss of funds to msg.sender, no way to recover).

### Likelihood Explanation
Likelihood is significant: `payable` functions taking a computed total and using `>=` are a common integration pattern, and it is easy for a caller (wallet UI, script, or another contract composing calls) to overestimate `msg.value` (e.g., rounding when calculating `amount * recipients.length`, or reusing a slightly-too-large value across similar calls). No special privilege or malicious actor is required — a single honest but imprecise transaction triggers the loss.

### Recommendation
Add an explicit refund of the excess value to `msg.sender` at the end of each function, e.g.:
```solidity
uint256 totalAmount = amount * recipients.length;
require(msg.value >= totalAmount, "Insufficient amount sent");
...
if (msg.value > totalAmount) {
    (bool refunded, ) = payable(msg.sender).call{value: msg.value - totalAmount}("");
    require(refunded, "Refund failed");
}
```
Apply the equivalent fix to `batchTransfer()`. Alternatively, tighten the check to `require(msg.value == totalAmount, ...)` so no excess can ever be accepted, removing the need for a refund path.

### Proof of Concept
1. Deploy `MultiSender`.
2. Call `batchTransferEqualAmount([recipientA, recipientB], amount)` with `msg.value = 2*amount + 1 wei` (or any amount `> totalAmount`).
3. The `require(msg.value >= totalAmount, ...)` check passes.
4. The loop sends `amount` to each of `recipientA` and `recipientB`, consuming `2*amount` of the sent value.
5. The remaining `1 wei` (or larger overpayment) stays in the `MultiSender` contract balance; `msg.sender` is never refunded, and no function in the contract can retrieve it. Repeating this with larger overpayments (e.g., off by a full `amount` unit due to a caller-side computation mismatch) causes proportionally larger permanent fund loss. [3](#0-2)

### Citations

**File:** contracts/src/MultiSender.sol (L1-46)
```text
// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.0;

contract MultiSender {
  event SendSuccessful(
    address indexed sender,
    address recipient,
    uint256 amount
  );

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
}
```
