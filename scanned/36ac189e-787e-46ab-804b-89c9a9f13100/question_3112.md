# Q3112: Routing-To-Wrong Asset With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept` with attacker-controlled send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `on_accept` send callback or timeout handling through the wrong local asset or wrong pallet account context so `the pallet-side asset routing context` becomes inconsistent with `the exact asset and pallet account authenticated by the request path`, breaking the invariant that receive, response, and timeout callbacks must route to the exact local asset and pallet account implied by the authenticated message and leading to High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value?

## Target
- File/function: modules/pallets/hyper-fungible-token/src/module.rs::on_accept
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept
- Attacker controls: send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs
- Exploit idea: Send callback or timeout handling through the wrong local asset or wrong pallet account context. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: receive, response, and timeout callbacks must route to the exact local asset and pallet account implied by the authenticated message
- Expected Immunefi impact: High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value.
- Fast validation: Vary asset ids and remote contracts across adjacent transfers and assert callbacks touch only the intended asset account. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
