### Title
`MultiSender`: Overpaid ETH in batch-transfer functions is never refunded, permanently locking excess funds - ([File: contracts/src/MultiSender.sol])

### Summary
`MultiSender.sol`'s `batchTransferEqualAmount()` and `batchTransfer()` are `payable` functions that only check `msg.value >= totalAmount` and never refund any excess ETH sent above the computed `totalAmount`, mirroring the reported `FootiumAcademy.mintPlayers()` excess-fee issue.

### Finding Description
Both batch-send entry points accept an arbitrary `msg.value` and validate only a lower bound: [1](#0-0) [2](#0-1) 

`batchTransferEqualAmount` computes `totalAmount = amount * recipients.length` and requires `msg.value >= totalAmount`, then `send()`s exactly `amount` to each recipient. `batchTransfer` sums `amounts[]` into `totalAmount`, requires `msg.value >= totalAmount`, then sends each `amounts[i]` to its recipient. In neither function is `msg.value - totalAmount` ever transferred back to `msg.sender`, and the contract has no `withdraw`/`sweep` function reachable by the caller. Any wei sent beyond the exact sum of transfers is retained by the contract with no code path to recover it — this is structurally identical to the reported `FootiumAcademy.mintPlayers()` bug where `totalFee = playerCount * divisionToFee[divisionTier]` is validated against `msg.value` but the surplus is never returned.

### Impact Explanation
Since `MultiSender` has no owner-only recovery function or refund logic, any excess ETH sent by a caller (e.g., an off-by-one miscalculation of `totalAmount`, or a caller intentionally/accidentally overpaying) is permanently locked in the contract with no way for anyone — including the original sender — to retrieve it. This is a direct, unrecoverable fund-loss condition triggerable purely by a normal transaction sender interacting with a deployed instance of this contract; because value transfer failures already `require(success)` revert the whole call, the only way funds get stuck is via legitimate overpayment, and that ETH is trapped for good.

### Likelihood Explanation
Likelihood is realistic: any user calling `batchTransferEqualAmount` or `batchTransfer` with `msg.value` set even slightly above the exact intended total (rounding, gas estimation tooling padding value, or simple user error) permanently loses the difference. No special privileges or malicious actors are required — a single unprivileged EVM transaction sender triggers the bug.

### Recommendation
At the end of both `batchTransferEqualAmount` and `batchTransfer`, after distributing funds to all recipients, compute the leftover `msg.value - totalAmount` and refund it back to `msg.sender` via a checked call, e.g.:
```solidity
uint256 excess = msg.value - totalAmount;
if (excess > 0) {
    (bool refunded, ) = payable(msg.sender).call{value: excess}("");
    require(refunded, "Failed to refund excess ETH");
}
```
Alternatively, require `msg.value == totalAmount` exactly to reject overpayment outright.

### Proof of Concept
1. Deploy `MultiSender`.
2. Call `batchTransferEqualAmount([recipient], amount)` with `msg.value = amount + 1 ether`.
3. The loop sends exactly `amount` to `recipient`; the function returns successfully because `msg.value >= totalAmount` holds.
4. `1 ether` remains in the `MultiSender` contract's balance with no function available to withdraw it — the funds are permanently stuck, confirmed by inspecting [3](#0-2)  which contains no refund or sweep logic anywhere in the contract.

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
