Looking at the vulnerability class from the external report — a failed token transfer to one user permanently blocking a protocol-level state transition — I need to find an analogous pattern in the Omni Bridge production files.

Let me examine the EVM `finTransfer` flow and the NEAR bridge's transfer storage more carefully.