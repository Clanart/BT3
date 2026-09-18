All matches for the reported bug class (on-chain slippage/`min_amount_out` computation gating a liquidation swap that a sandwich attacker can force to revert) live only in test fixtures, load generator contracts, and third-party AMM router copies (`integration_test/load_generator/**`, `loadtest/contracts/**`, `contracts/src/ProxySwapTester.sol`), none of which are production sei-chain consensus code. There is no in-scope margin/perp/liquidation module with an on-chain-computed slippage bound gating a DEX swap. The former `x/dex` liquidation logic was explicitly removed from the chain per `CHANGELOG.md` ("Remove liquidation logic from dex"), and no such min-out/slippage computation exists in `x/dex`, `x/evm`, `precompiles`, `x/tokenfactory`, or `x/bank`. [1](#0-0) [2](#0-1) 

#No vulnerability found for this question.

### Citations

**File:** CHANGELOG.md (L1902-1906)
```markdown
## 2.0.48beta
sei-chain:
* [#743](https://github.com/sei-protocol/sei-chain/pull/743) Do not unregister contract if out of rent
* [#742](https://github.com/sei-protocol/sei-chain/pull/742) Add more metrics to dex module
* [#733](https://github.com/sei-protocol/sei-chain/pull/733) Remove liquidation logic from dex
```

**File:** loadtest/contracts/mars/src/contract.rs (L83-105)
```rust
pub fn process_bulk_liquidation(
    _deps: DepsMut<SeiQueryWrapper>,
    _env: Env,
    _requests: Vec<LiquidationRequest>,
) -> Result<Response, StdError> {
    let response = LiquidationResponse {
        successful_accounts: vec![],
        liquidation_orders: vec![],
    };
    let serialized_json = match serde_json::to_string(&response) {
        Ok(val) => val,
        Err(error) => panic!("Problem parsing response: {:?}", error),
    };
    let base64_json_str = base64::encode(serialized_json);
    let binary = match Binary::from_base64(base64_json_str.as_ref()) {
        Ok(val) => val,
        Err(error) => panic!("Problem converting binary for order request: {:?}", error),
    };

    let mut response: Response = Response::new();
    response = response.set_data(binary);
    Ok(response)
}
```
