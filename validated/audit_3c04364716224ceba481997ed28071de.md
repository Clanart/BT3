### Title
`pay()` Uses `safeTransfer` Instead of `safeTransferFrom` for Non-WETH External Payer, Permanently Breaking All Non-WETH Swaps — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` helper in `PeripheryPayments.sol` contains a wrong-operation bug in its final `else` branch: it calls `IERC20(token).safeTransfer(recipient, value)` (a push from the router's own balance) instead of `IERC20(token