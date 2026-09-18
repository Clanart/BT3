### Title
Overpayment to `MultiSender` batch-transfer functions permanently locks user funds - (File: contracts/src/MultiSender.sol)

### Summary
`MultiSender.sol` implements `batchTransferEqualAmount` and `batchTransfer`, both `payable` functions that distribute `msg.value` across a list of recipients. Both functions validate `msg.value >= totalAmount` instead of strict equality, and neither refunds nor tracks any leftover balance. Any excess wei sent above the sum of the per-recipient amounts is permanently stranded in the contract, mirroring the Gitcoin `RoundImplementation.vote()` finding referenced in the external report.

### Finding Description
In `batchTransferEqualAmount`: [1](#0-0) 

and in `batchTransfer`: [2](#0-1) 

Both functions compute `totalAmount` (the sum of what will actually be sent out to recipients) and then only assert `msg.value >= totalAmount` rather than `msg.value == totalAmount`. The functions then loop and `send()` exactly `totalAmount` in wei to the recipients, leaving `msg.value - totalAmount` sitting in the `MultiSender` contract's own balance. The contract defines no `receive()`/`fallback()`, no withdrawal function, and no owner/admin able to reclaim funds — so any wei sent in excess of `totalAmount` is unrecoverable by the sender or anyone else.

This is functionally identical to the reported Gitcoin bug class: a `payable` function that iterates over a set of encoded/explicit amounts and transfers exactly those amounts, without reconciling that `msg.value` equals the sum, and without any refund path for the difference.

### Impact Explanation
Any caller who overestimates `msg.value` relative to the actual sum of `amounts` (e.g., due to a client-side calculation error, off-by-one, floating point rounding when constructing the transaction, or simply sending a round number like 1 SEI while the sum of amounts is less) will have the excess amount permanently locked in the `MultiSender` contract with no way to retrieve it. This is a direct, unrecoverable loss of user funds — meeting the "concrete fund loss or permanent freezing" bar for this scan.

### Likelihood Explanation
`MultiSender` is a public, unrestricted contract: `batchTransferEqualAmount` and `batchTransfer` are `external payable` with no access control, so any EOA or contract can call them directly through the EVM. The trigger condition (sending `msg.value` slightly larger than the true sum of amounts) is easy to hit accidentally — it requires no adversary, only a client mis-calculating the value field, which is a common real-world mistake for batch-payment tooling. This matches the "unprivileged transaction sender" / "contract deployer" reachable surface required for this scan.

### Recommendation
Change the check in both functions to strict equality (`require(msg.value == totalAmount, ...)`), or alternatively keep `>=` but refund the difference (`msg.value - totalAmount`) back to `msg.sender` after the distribution loop, e.g.:
```solidity
uint256 leftover = msg.value - totalAmount;
if (leftover > 0) {
    (bool refunded, ) = payable(msg.sender).call{value: leftover}("");
    require(refunded, "Refund failed");
}
```

### Proof of Concept
1. Caller invokes `batchTransfer([recipientA, recipientB], [1 ether, 1 ether])` while sending `msg.value = 2.5 ether`.
2. `totalAmount` is computed as `2 ether`; the `require(msg.value >= totalAmount, ...)` check at [3](#0-2)  passes since `2.5 ether >= 2 ether`.
3. The loop at [4](#0-3)  sends exactly `1 ether` to each recipient (`2 ether` total).
4. The remaining `0.5 ether` stays in the `MultiSender` contract's balance. Since the contract has no `withdraw`, `receive`, or `fallback` function, this `0.5 ether` is permanently locked and unrecoverable by the caller or anyone else.

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
