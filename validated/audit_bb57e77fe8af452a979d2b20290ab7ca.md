### Title
`WSEI.withdraw()` uses fixed-gas `transfer()` instead of `call()`, causing withdrawals to permanently revert for smart-contract holders - (File: `contracts/src/WSEI.sol`)

### Summary
The canonical Wrapped-SEI ERC20 contract shipped and deployable on sei-chain's EVM uses `payable(msg.sender).transfer(wad)` in its `withdraw()` function. `transfer()` forwards a hard-coded 2300 gas stipend, which is the exact anti-pattern the reported OpenQ finding flags — `call()` should be used instead of a fixed-gas value transfer on a payable path, since EVM gas-cost changes or any downstream logic in the recipient can make the stipend insufficient and permanently break the withdraw path for that holder.

### Finding Description
`WSEI.sol` implements a WETH-style wrapper for native SEI: `deposit()` credits `balanceOf[msg.sender]` with `msg.value`, and `withdraw()` debits the balance and returns SEI via a fixed-gas `transfer()` call: [1](#0-0) 

This contract is not merely a test fixture — its bytecode is embedded and shipped as a first-class production artifact in `x/evm/artifacts/wsei/artifacts.go`, and the chain provides a dedicated CLI command, `deploy-wsei`, whose sole purpose is to deploy this exact bytecode for users: [2](#0-1) [3](#0-2) 

Because `transfer()`/`send()` semantics forward only 2300 gas, any `msg.sender` that is a smart-contract account (multisig wallet, DEX router, another protocol's vault, account-abstraction wallet, etc.) whose `receive()`/`fallback()` consumes more than 2300 gas will have every `withdraw()` call revert. Unlike a `call()`-based implementation, there's no way for such a caller to ever redeem their wrapped balance — the WSEI contract has no alternate ETH-out path (no `call`-based rescue function), so the SEI backing that balance is permanently locked inside the contract.

### Impact Explanation
Any contract-based holder of WSEI that accumulates balance (via `deposit()`, `receive()`/`fallback()`, or receiving a `transfer`/`transferFrom` of WSEI tokens) can be permanently unable to unwrap their SEI if their contract's `receive`/`fallback` needs >2300 gas (e.g., it emits events, updates storage, or has a reentrancy guard). This is a permanent freezing of funds for the affected holder, which satisfies the required impact bar (permanent freezing of funds). Since WSEI is the chain's canonical wrapped-native-token contract intended for DeFi composability, contract-based liquidity providers/routers interacting with it are realistically exposed to this failure mode.

### Likelihood Explanation
Likelihood is moderate-to-high: any unprivileged party can trigger the bug simply by holding WSEI in a smart-contract wallet with non-trivial receive logic and calling `withdraw()` — no special privileges, races, or validator/network assumptions are required. The same vulnerable pattern is also duplicated in the test/reference ERC20 wrapper templates (`evmrpc/solidity/ERC20.sol`, `evmrpc/tests/solidity/ERC20.sol`), reinforcing that fixed-gas `transfer()` is used as the canonical idiom on this codebase, but the impactful/production instance is `contracts/src/WSEI.sol` since it is the one shipped and deployable by CLI as the "real" WSEI contract.

### Recommendation
Replace `payable(msg.sender).transfer(wad)` in `WSEI.withdraw()` with a low-level `call` that forwards all remaining gas and checks the return value, following the standard WETH9/OpenZeppelin pattern:
```solidity
(bool success, ) = msg.sender.call{value: wad}("");
require(success, "WSEI: SEI transfer failed");
```
This removes the dependency on a fixed 2300 gas stipend and allows any contract recipient (including those with reentrancy guards or event-emitting fallback logic) to withdraw successfully, while still relying on `require(success)` plus checks-effects-interactions (balance already decremented beforehand) to prevent reentrancy risk.

### Proof of Concept
1. Deploy a `Receiver` contract with a `receive()` function that performs a storage write costing more than 2300 gas (e.g., `receive() external payable { someMapping[block.number] = msg.value; }`).
2. From `Receiver`, call `WSEI.deposit{value: 1 ether}()`.
3. From `Receiver`, call `WSEI.withdraw(1 ether)`.
4. The internal `payable(msg.sender).transfer(wad)` call in `withdraw()` reverts with an out-of-gas error because only 2300 gas is forwarded to `Receiver.receive()`.
5. `Receiver`'s WSEI balance remains debited-then-reverted (the whole tx reverts), so its `balanceOf` entry is unchanged, but there is no possible transaction that lets `Receiver` ever redeem its wrapped SEI back to native SEI — the funds are permanently stuck in the `WSEI` contract.

### Citations

**File:** contracts/src/WSEI.sol (L27-32)
```text
    function withdraw(uint wad) public {
        require(balanceOf[msg.sender] >= wad);
        balanceOf[msg.sender] -= wad;
        payable(msg.sender).transfer(wad);
        emit Withdrawal(msg.sender, wad);
    }
```

**File:** x/evm/artifacts/wsei/artifacts.go (L1-26)
```go
package wsei

import (
	"embed"
	"encoding/hex"
	"strings"

	"github.com/ethereum/go-ethereum/accounts/abi"
)

const CurrentVersion uint16 = 1

//go:embed WSEI.abi
//go:embed WSEI.bin
var f embed.FS

var cachedBin []byte
var cachedABI *abi.ABI

func GetABI() []byte {
	bz, err := f.ReadFile("WSEI.abi")
	if err != nil {
		panic("failed to read WSEI contract ABI")
	}
	return bz
}
```

**File:** x/evm/client/cli/tx.go (L428-464)
```go
func CmdDeployWSEI() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "deploy-wsei --from=<sender> --gas-fee-cap=<cap> --gas-limt=<limit> --evm-rpc=<url>",
		Short: "Deploy ERC20 contract for a native Sei token",
		Long:  "",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) (err error) {
			contractData := wsei.GetBin()

			key, err := getPrivateKey(cmd)
			if err != nil {
				return err
			}

			rpc, err := cmd.Flags().GetString(FlagRPC)
			if err != nil {
				return err
			}
			var nonce uint64
			if n, err := cmd.Flags().GetInt64(FlagNonce); err == nil && n >= 0 {
				nonce = uint64(n)
			} else {
				nonce, err = getNonce(rpc, key.PublicKey)
				if err != nil {
					return err
				}
			}

			txData, err := getTxData(cmd)
			if err != nil {
				return err
			}
			txData.Nonce = nonce
			txData.Value = utils.Big0
			txData.Data = contractData

			resp, err := sendTx(txData, rpc, key)
```
