### Title
`MultiSender` permanently locks overpaid native SEI with no refund or rescue mechanism - ([File: contracts/src/MultiSender.sol])

### Summary
`MultiSender.batchTransferEqualAmount` and `MultiSender.batchTransfer` only require `msg.value >= totalAmount` rather than an exact match, and neither function refunds the excess `msg.value`, nor does the contract expose any rescue/withdraw function. Any wei sent above the computed `totalAmount` is stranded in the contract balance permanently, exactly the "overpaid amount can not be retrieved" bug class described in the external report — except here there is no `rescueERC20`-equivalent function at all, so the excess is unrecoverable by anyone, including the deployer.

### Finding Description
Both payable entry points compute the exact amount needed to fulfill the batch transfer and only check a lower bound: [1](#0-0) [2](#0-1) 

In `batchTransferEqualAmount`, `totalAmount = amount * recipients.length`, and the code only asserts `msg.value >= totalAmount` before distributing exactly `amount` to each recipient via `payable(recipients[i]).send(amount)`. Any `msg.value` in excess of `totalAmount` is never sent anywhere and remains in the contract's balance. The same pattern exists in `batchTransfer`, which sums `amounts[]` into `totalAmount` and only forwards each element of `amounts[]`, leaving any surplus `msg.value` stuck.

Critically, `MultiSender` has no `owner`, no `withdraw`/`rescue`/`sweep` function, and no `receive`/`fallback` logic that could later move out the balance — the entire contract, reproduced in full above, consists solely of these two functions and an event. This is functionally identical to (and strictly worse than) the reported `rescueERC20` issue: in the Nounsdao `Stream.sol` case at least a restrictive rescue function existed but blocked clawback of the stream token; here, no clawback path exists whatsoever for the overpaid SEI.

### Impact Explanation
Any unprivileged EVM account that calls `batchTransferEqualAmount` or `batchTransfer` with `msg.value` greater than the exact required total (e.g., due to a wallet's gas/value estimation, a rounding mistake, or by directly overshooting) permanently loses the difference — those funds become irrecoverably locked in the contract, matching the "permanent freezing of funds" impact category, since there is no owner-only or any other pathway to reclaim them.

### Likelihood Explanation
Likelihood is meaningful because the check is a strict inequality (`>=`) rather than equality, so nothing prevents a caller from attaching more `wei`/`usei` than needed; this can happen accidentally (common in wallets/dApps that round up gas/value) or be triggered deliberately by any user interacting with the publicly reachable contract, with no special privileges required.

### Recommendation
Either (a) enforce `msg.value == totalAmount` so any mismatch reverts the whole transaction, safely returning funds to the sender at the EVM level, or (b) if overpayment should be permitted, explicitly refund the excess (`msg.value - totalAmount`) back to `msg.sender` at the end of each function, mirroring how a proper `rescueERC20`/refund path should let the payer claw back any surplus they sent.

### Proof of Concept
1. Deploy `MultiSender`.
2. Call `batchTransferEqualAmount([recipientA], 1 ether)` with `msg.value = 2 ether`.
3. `totalAmount` is computed as `1 ether`; the `require(msg.value >= totalAmount, ...)` check passes.
4. The loop sends exactly `1 ether` to `recipientA`.
5. The remaining `1 ether` stays in the `MultiSender` contract's balance; there is no function in the contract that can move it out again — it is permanently lost to the caller.

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

**File:** contracts/src/MultiSender.sol (L25-46)
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
}
```
