### Title
CW20 wrapper for arbitrary ERC20 tokens accepts non-reverting failed transfers as success - (File: `example/cosmwasm/cw20/src/contract.rs`)

### Summary
The example CW20-over-ERC20 pointer contract wraps an arbitrary EVM ERC20 token as a CosmWasm CW20 token. Its `transfer` and `transfer_from` handlers build ERC20 calldata via the EVM query bindings and dispatch it with `EvmMsg::DelegateCallEvm`, then immediately return a "success" `Response` without checking the boolean return value that the wrapped ERC20 encodes in its call output.

### Finding Description
`transfer` and `transfer_from` in `example/cosmwasm/cw20/src/contract.rs` fetch ABI-encoded ERC20 `transfer`/`transferFrom` payloads via `EvmQuerier::erc20_transfer_payload` / `erc20_transfer_from_payload` and dispatch them through `EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload }`, then unconditionally build a success `Response` with `from`/`to`/`amount` attributes: [1](#0-0) [2](#0-1) 

The construction of the ERC20 transfer/transferFrom payload happens through the wasmd query bindings, which do not carry any expectation that the resulting call's ABI-encoded `bool` return value be inspected by the caller: [3](#0-2) 

Just as in the reported Cooler.sol issue, per EIP-20/"weird ERC20" semantics, a non-standard ERC20 token can return `false` from `transfer`/`transferFrom` instead of reverting on failure (e.g., zero-address checks, blacklist, paused state, insufficient balance in some non-standard implementations). Because `DelegateCallEvm` only fails when the underlying EVM call itself reverts, and the CW20 wrapper contract never decodes/checks the ABI-encoded boolean return data, a failed-but-non-reverting ERC20 transfer will still result in a successful CosmWasm `Response` from the pointer contract, with `Transfer`/action attributes implying that value moved when it did not.

### Impact Explanation
Any Cosmos-side consumer of this CW20 pointer (marketplaces, lending/escrow CW contracts, or other integrators built on top of pointer tokens) that trusts a successful `execute` result as proof that tokens moved can be defrauded exactly like the lender in the Cooler.sol report: a counterparty using a weird/malicious wrapped ERC20 as collateral or payment can appear to "transfer" tokens that never actually left their balance, while the receiving/relying contract proceeds as if payment/collateral was received. This can lead to concrete fund loss for any party that conditions further logic (e.g., releasing debt, NFTs, or other assets) on the apparent success of the wrapped transfer.

### Likelihood Explanation
Reachable by any CosmWasm user who deploys/uses a CW20 pointer wrapping an ERC20 they control, choosing a token whose `transfer`/`transferFrom` returns `false` on failure instead of reverting. No privileged access is required — a single wasm `ExecuteMsg::TransferFrom` message on the pointer contract, from an unprivileged CosmWasm user, is sufficient to trigger the silently-failing path.

### Recommendation
After dispatching the `DelegateCallEvm` message, decode the returned data as the ABI-encoded `bool` and abort/`ContractError` if it decodes to `false` (or if data is non-empty and not `true`), mirroring OpenZeppelin's `SafeERC20`/`TransferHelper` pattern (`success && (data.length == 0 || abi.decode(data, (bool)))`), instead of relying solely on whether the underlying EVM call reverted.

### Proof of Concept
1. Deploy a "weird" ERC20 contract on the EVM side whose `transfer`/`transferFrom` returns `false` (without reverting) when the caller's balance/allowance is insufficient.
2. Instantiate the example CW20 pointer contract (`example/cosmwasm/cw20`) with `erc_addr` set to this weird ERC20.
3. As an unprivileged CosmWasm user with zero balance/allowance on the underlying ERC20, call `ExecuteMsg::TransferFrom { owner, recipient, amount }` (or `Transfer`) on the CW20 pointer.
4. Observe that `transfer_from`/`transfer` in `contract.rs` returns a successful `Response` with `action`, `from`, `to`, `amount` attributes even though the underlying ERC20 `transferFrom` call returned `false` and no balance actually moved, because `DelegateCallEvm` only errors on EVM revert, and the wrapper never inspects the encoded boolean result.

### Citations

**File:** example/cosmwasm/cw20/src/contract.rs (L227-248)
```rust
fn transfer(
    deps: DepsMut<EvmQueryWrapper>,
    _env: Env,
    info: MessageInfo,
    recipient: String,
    amount: Uint128,
) -> Result<Response<EvmMsg>, ContractError> {
    deps.api.addr_validate(&recipient)?;

    let erc_addr = ERC20_ADDRESS.load(deps.storage)?;

    let querier = EvmQuerier::new(&deps.querier);
    let payload = querier.erc20_transfer_payload(recipient.clone(), amount)?;
    let msg = EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload };
    let res = Response::new()
        .add_attribute("from", info.sender)
        .add_attribute("to", recipient)
        .add_attribute("amount", amount)
        .add_message(msg);

    Ok(res)
}
```

**File:** example/cosmwasm/cw20/src/contract.rs (L250-274)
```rust
pub fn transfer_from(
    deps: DepsMut<EvmQueryWrapper>,
    _env: Env,
    info: MessageInfo,
    owner: String,
    recipient: String,
    amount: Uint128,
) -> Result<Response<EvmMsg>, ContractError> {
    deps.api.addr_validate(&owner)?;
    deps.api.addr_validate(&recipient)?;

    let erc_addr = ERC20_ADDRESS.load(deps.storage)?;

    let querier = EvmQuerier::new(&deps.querier);
    let payload = querier.erc20_transfer_from_payload(owner.clone(), recipient.clone(), amount)?;
    let msg = EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload };
    let res = Response::new()
        .add_attribute("from", owner)
        .add_attribute("to", recipient)
        .add_attribute("by", info.sender)
        .add_attribute("amount", amount)
        .add_message(msg);

    Ok(res)
}
```

**File:** x/evm/client/wasm/bindings/queries.go (L185-194)
```go
type ERC20TransferPayloadRequest struct {
	Recipient string   `json:"recipient"`
	Amount    *sdk.Int `json:"amount"`
}

type ERC20TransferFromPayloadRequest struct {
	Owner     string   `json:"owner"`
	Recipient string   `json:"recipient"`
	Amount    *sdk.Int `json:"amount"`
}
```
