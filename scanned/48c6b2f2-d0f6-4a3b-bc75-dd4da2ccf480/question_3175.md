# Q3175: `get_tip_header_chain_proof` and height/finality boundaries

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees exploit the finality or confirmation-depth boundary used by `get_tip_header_chain_proof` in `core/src/header_chain_prover.rs` - acting at exactly the boundary height, or across a range whose start/end are computed inconsistently - so the bridge commits to a fact at a depth from which it can still be reversed?

## Target
- File/function: `core/src/header_chain_prover.rs` -> `get_tip_header_chain_proof` (This module contains utilities for proving Bitcoin block headers. This)
- Entrypoint: transaction placement around the finality boundary -> `get_tip_header_chain_proof`
- Attacker controls: the height at which the attacker's transaction confirms; attacker is an unprivileged party who can broadcast Bitcoin transactions and pay fees; holds no protocol role or key
- Exploit idea: commit the protocol to a reversible fact
- Invariant to test: the depth at which a fact is committed is at least the configured finality depth in every path
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: assert boundary heights are handled identically across all consumers of `get_tip_header_chain_proof`
