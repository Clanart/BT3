Looking at the external report's invariant: **unprotected functions that can be called by anyone, combined with `msg.sender` being used as a privileged beneficiary, allow an attacker to redirect or steal value that belongs to another user.**

Let me trace this through the Metric OMM payment path.