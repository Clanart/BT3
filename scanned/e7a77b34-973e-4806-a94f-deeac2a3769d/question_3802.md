# Q3802: EVM OmniBridge native/bridged branching global asset-conservation invariant break

## Question
Can an unprivileged attacker combine the public surface behind `public EVM init/finalize entrypoints` with the code paths summarized by `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer` and make total redeemable claims across chains exceed the total burned, locked, or custodied assets tracked by selects between ETH release, ERC-1155 transfer, custom-minter mint/burn, bridge-token mint/burn, or plain ERC-20 custody based on mutable branch predicates, violating `branch selection must never be attacker-steerable toward a more profitable asset path than the one the signed or emitted payload actually authorizes`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer`
- Entrypoint: `public EVM init/finalize entrypoints`
- Attacker controls: zero address versus token address, custom-minter registration, bridge-token registration, and message presence
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class.
- Invariant to test: branch selection must never be attacker-steerable toward a more profitable asset path than the one the signed or emitted payload actually authorizes
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step.
