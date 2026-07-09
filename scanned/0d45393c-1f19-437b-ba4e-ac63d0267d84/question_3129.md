# Q3129: EVM OmniBridge native/bridged branching burn debits the wrong logical account at boundary values

## Question
Can an unprivileged attacker trigger `public EVM init/finalize entrypoints` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer` violate `branch selection must never be attacker-steerable toward a more profitable asset path than the one the signed or emitted payload actually authorizes` in the `burn debits the wrong logical account` attack class because selects between ETH release, ERC-1155 transfer, custom-minter mint/burn, bridge-token mint/burn, or plain ERC-20 custody based on mutable branch predicates becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer`
- Entrypoint: `public EVM init/finalize entrypoints`
- Attacker controls: zero address versus token address, custom-minter registration, bridge-token registration, and message presence
- Exploit idea: Target burns keyed to predecessor account, owner, or controller context rather than an explicit subject. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: branch selection must never be attacker-steerable toward a more profitable asset path than the one the signed or emitted payload actually authorizes
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Manipulate caller/proxy layouts and assert that the debited balance always belongs to the asset owner represented in the bridge event. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
