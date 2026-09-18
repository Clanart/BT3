The RubiconRouter finding (unsafe `.transfer()` for ETH payouts) has a direct analog in the sei-chain codebase in the WSEI (Wrapped SEI) contract.

### Title
Permanent fund freeze via 2300-gas `.transfer()` stipend in WSEI.withdraw() - (File: contracts/src/WSEI.sol)

### Summary
The canonical Wrapped SEI (WSEI) contract, whose bytecode is embedded in the chain binary and deployable by any EVM transaction sender via the `deploy-wsei` CLI command, uses Solidity's `payable(msg.sender).transfer(wad)` to return native SEI on withdrawal. This method forwards only a fixed 2300 gas stipend to the recipient, the exact anti-pattern flagged in the RubiconRouter finding.

### Finding Description
`WSEI.withdraw()` decrements the caller's WSEI balance and then unwraps it back to native SEI using `.transfer()`: [1](#0-0) 

The same pattern exists in the `deposit()`/`withdraw()` flow, and identically in the test/mock ERC20 contract that mirrors WSEI's shape: [2](#0-1) 

The WSEI bytecode and ABI are packaged directly into the sei-chain binary and used to deploy the production Wrapped SEI contract: [3](#0-2) 

Any unprivileged user can trigger this deployment via the public `deploy-wsei` CLI command, which any transaction sender can invoke to instantiate this exact bytecode on-chain: [4](#0-3) 

Once deployed, `withdraw()` is reachable by any EVM account, including smart-contract wallets (e.g., proxy wallets, multisigs) that deposited native SEI to receive WSEI.

### Impact Explanation
Solidity's `.transfer()`/`.send()` forward a hardcoded 2300 gas stipend. If `msg.sender` is a smart contract whose `receive()`/`fallback()` function consumes more than 2300 gas (common for proxy contracts performing a `SLOAD`+`DELEGATECALL`, or wallets with any nontrivial fallback logic — and increasingly likely given historical gas-cost repricings such as EIP-1884 raising `SLOAD` cost), the forwarded call will run out of gas and revert. Since `withdraw()` has no fallback path (e.g., no `call()`-based transfer), the caller's WSEI balance can never be redeemed for native SEI through this function, resulting in a permanent freezing of funds for any contract-based holder whose fallback exceeds the 2300 gas stipend.

### Likelihood Explanation
Likelihood is moderate-to-high: any contract wallet, multisig, or proxy that wraps SEI into WSEI (a natural, encouraged use case for the canonical wrapped-native-token contract) and whose receiving logic exceeds 2300 gas will be permanently unable to unwrap. This requires no malicious actor — it's a self-inflicted freeze triggered by ordinary usage from contract accounts, and is exactly the class of failure the original finding describes.

### Recommendation
Replace `payable(msg.sender).transfer(wad)` in `WSEI.withdraw()` with a low-level `call()` and check the return value:
```solidity
(bool success, ) = payable(msg.sender).call{value: wad}("");
require(success, "SEI transfer failed");
```
Apply the same pattern to any other production Solidity contracts using `.transfer()`/`.send()` for native-value payouts, and re-audit the embedded bytecode artifacts to ensure the fix is reflected in `x/evm/artifacts/wsei/WSEI.bin`.

### Proof of Concept
1. Deploy a minimal smart-contract wallet whose `receive()` function performs at least one `SLOAD` plus a few arithmetic operations (easily exceeding 2300 gas, e.g., updating a state variable).
2. From that contract, call `WSEI.deposit()` with some SEI value to mint WSEI balance.
3. From the same contract, call `WSEI.withdraw(wad)`.
4. Observe the transaction reverts because the internal `payable(msg.sender).transfer(wad)` call runs out of the 2300 gas stipend inside the recipient's `receive()`, permanently locking the contract's WSEI balance (it can never successfully call `withdraw()`). [1](#0-0)

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

**File:** evmrpc/solidity/ERC20.sol (L22-27)
```text
    function withdraw(uint256 wad) public {
        require(balanceOf[msg.sender] >= wad);
        balanceOf[msg.sender] -= wad;
        payable(msg.sender).transfer(wad);
        emit Withdrawal(msg.sender, wad);
    }
```

**File:** x/evm/artifacts/wsei/artifacts.go (L13-26)
```go
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

**File:** x/evm/client/cli/tx.go (L428-463)
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

```
