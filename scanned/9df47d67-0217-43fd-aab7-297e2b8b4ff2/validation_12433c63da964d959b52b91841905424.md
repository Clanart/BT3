### Title
Residual ETH in Router Consumed by Subsequent Caller's WETH Swap, Causing Direct ETH Loss - (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
The `pay` function in `PeripheryPayments.sol` uses the router's entire `address(this).balance` — not just the ETH attributable to the current caller — to fund