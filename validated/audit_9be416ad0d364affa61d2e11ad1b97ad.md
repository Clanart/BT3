### Title
Stale `feemarketParams` Captured at Construction Time in `newCosmosAnteHandler` Enables EIP-1559 Base Fee Bypass for Cosmos Transactions - (File: `evmd/ante/handler_options.go`)

### Summary
`newCosmosAnteHandler` captures `feemarketParams` (including `BaseFee`) once at construction time and passes a