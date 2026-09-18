### Title
Unrefunded excess ETH permanently locked in `MultiSender` contract - (File: contracts/src/MultiSender.sol)

### Summary
`MultiSender.sol`'s `batchTransferEqualAmount` and `batchTransfer` functions accept `msg.value` and only require `msg.value >= totalAmount`, but they distribute only `amount`/`amounts[i]` per recipient and never refund or otherwise account for the excess `msg.value - totalAmount`. Any excess ETH sent by the caller is stranded in the contract with no withdrawal mechanism, matching the reported bug class ("if the user sends more than the required amount, the excess is not returned").

### Finding Description
Both payable entry points use a `>=` check against the required total instead of an exact match, and neither function returns unused funds to `msg.sender`: [1](#0-0) [2](#0-1) 

In `batchTransferEqualAmount`, `totalAmount = amount * recipients.length` is computed, the check `require(msg.value >= totalAmount, ...)` passes for any value greater than or equal to the required total, and then only `amount` is sent to each recipient via `payable(recipients[i]).send(amount)`. Any `msg.value` above `totalAmount` remains in the contract's balance. The same pattern applies in `batchTransfer`, which sums `amounts[i]` and validates with `msg.value >= totalAmount`. There is no `receive`/`withdraw`/refund logic anywhere in the 46-line contract, so overpaid ETH is not just left unrefunded to the sender in the same transaction — it is permanently trapped in the contract because there is no function to move it out.

This is the exact defect class in the referenced audit report (`TokensFarm.sol` deposit function): a `>=` comparison used for a payable amount check, with no logic to return the difference, and the original recommendation ("replace `>=` with `==`") is the correct fix pattern that is missing here.

### Impact Explanation
Any unprivileged EVM caller who overestimates gas/slippage or miscalculates `totalAmount` and sends `msg.value` greater than the required sum will have the excess amount permanently frozen inside the contract, since no owner/rescue/withdraw function exists to later reclaim it. This is a direct, concrete loss of user funds reachable purely through an ordinary EVM contract call, satisfying the "permanent freezing"/"fund loss" impact bar.

### Likelihood Explanation
The bug triggers on every call where `msg.value` is not exactly equal to the computed total — a very easy and likely user/client error (e.g., off-by-one, stale amount calculation, wallet UX rounding, or intentional over-funding to be safe). No special privileges, front-running, or unusual conditions are required; a single, ordinary transaction is sufficient.

### Recommendation
Change the validation from `msg.value >= totalAmount` to `msg.value == totalAmount` (as was done for `TokensFarm.sol` in the referenced report), or alternatively compute the excess (`msg.value - totalAmount`) and refund it to `msg.sender` at the end of the function before returning. Also consider using `call` with checks-effects-interactions ordering (or a pull-payment pattern) instead of `send`, to guard against future reentrancy issues if refund logic is added.

### Proof of Concept
1. Deployer publishes `MultiSender` and any user calls `batchTransferEqualAmount([r1, r2], amount)` with `msg.value = 2*amount + 1 wei` (or any value greater than `totalAmount`).
2. The `require(msg.value >= totalAmount, ...)` check at [3](#0-2)  passes.
3. The loop sends exactly `amount` to each of `r1` and `r2` via `.send(amount)`.
4. The transaction succeeds; the 1 extra wei (or any larger excess) remains in the `MultiSender` contract's balance permanently, since the contract exposes no function to withdraw or return it to the original sender.

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
